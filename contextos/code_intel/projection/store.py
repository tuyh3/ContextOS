"""投影表读写: bulk 灌(executemany 分批) / 按 source_file 删(增量把手) / meta kv。

性能(spec §4): 大仓百万行级 references —— executemany 分批 50k, 全量先灌后建索引
交给 ensure_projection_schema 的建表期一次性建(SQLite/PG 建在前对 50k 批量影响
有限, 真实大仓实测吃紧再做 drop-index/rebuild 优化, 不预优化)。
replace_all_conn 是连接级变体: build 单事务 staging(灌新+抽样+meta 同事务,
超阈 raise 整体回滚 = 真保旧)的地基。
"""
from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Connection, Engine

from contextos.code_intel.projection import schema as S

_BATCH = 50_000

_TABLES_BY_NAME = {t.name: t for t in (S.code_files, *S.DATA_TABLES)}


def _insert_batched(conn: Connection, table: Any, rows: list[dict[str, Any]]) -> None:
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(table), rows[i:i + _BATCH])


def replace_all_conn(conn: Connection, rows_by_table: Mapping[str, list[dict[str, Any]]]) -> None:
    """在已打开事务上清空数据表 -> 灌新。"""
    for t in (S.code_files, *S.DATA_TABLES):
        conn.execute(delete(t))
    insert_rows_conn(conn, rows_by_table)


def replace_all(engine: Engine, rows_by_table: Mapping[str, list[dict[str, Any]]]) -> None:
    """全量替换(独立事务包装)。"""
    with engine.begin() as conn:
        replace_all_conn(conn, rows_by_table)


def insert_rows_conn(conn: Connection, rows_by_table: Mapping[str, list[dict[str, Any]]]) -> None:
    for name, rows in rows_by_table.items():
        if rows:
            _insert_batched(conn, _TABLES_BY_NAME[name], rows)


def delete_rows_for_files_conn(conn: Connection, files: list[str]) -> None:
    """增量把手: 删这些 source_file 的所有投影行(含 code_files 簿记行)。"""
    if not files:
        return
    for t in S.DATA_TABLES:
        conn.execute(delete(t).where(t.c.source_file.in_(files)))
    conn.execute(delete(S.code_files).where(S.code_files.c.file_path.in_(files)))


def set_meta(engine: Engine, key: str, value: str) -> None:
    with engine.begin() as conn:
        set_meta_conn(conn, key, value)


def set_meta_conn(conn: Connection, key: str, value: str) -> None:
    conn.execute(delete(S.code_projection_meta).where(S.code_projection_meta.c.key == key))
    conn.execute(insert(S.code_projection_meta), [{"key": key, "value": value}])


def get_meta(engine: Engine, key: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(select(S.code_projection_meta.c.value).where(
            S.code_projection_meta.c.key == key)).first()
    return row[0] if row else None


def table_counts(engine: Engine) -> dict[str, int]:
    out: dict[str, int] = {}
    with engine.connect() as conn:
        for t in (S.code_files, *S.DATA_TABLES):
            out[t.name] = int(conn.execute(select(func.count()).select_from(t)).scalar_one())
    return out
