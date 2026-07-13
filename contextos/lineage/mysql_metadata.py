"""MySQL 元数据抓取(spec 2026-07-10 附录 D)——Oracle refresh_metadata 的方言对位。

契约与 oracle_metadata.refresh_metadata 三条硬约束逐条对齐:
- 原子全量覆盖: 全拉成功后才 clear+write+盖时间戳(store.replace_metadata 单事务);
- 拉失败保旧快照: information_schema 查询任一步抛 -> refreshed=False, 旧快照原封不动;
- 空 databases: 不动旧快照、不盖时间戳(与 Oracle 空 owners 同语义)。

方言差异: 查 information_schema.TABLES/VIEWS 而非 ALL_* 字典视图; owner = database
名(MySQL schema); 一条 `TABLE_SCHEMA IN (...)` 批量覆盖同实例多库(附录 D.3)。
synonym/dblink 按能力矩阵(dialects.get_traits('mysql'))为空; 视图定义(VIEW_DEFINITION)
的 视图->表 血缘在 L3 接线期由 sqlglot 解析, 本层只把视图作为 dataset_type=VIEW 落 table_metadata。
querier = 任何有 .query(sql, params)->list[dict] 的对象(MySqlClient 满足; duck-type 同 Oracle)。
"""
from __future__ import annotations

from typing import Any, Protocol, Sequence

from contextos.lineage import store


class _Querier(Protocol):
    def query(self, sql: str, params: Any = None, **kw) -> list[dict]: ...


def _fetch_tables(querier: _Querier, databases: Sequence[str],
                  *, table_type: str) -> list[dict]:
    # 命名占位符 :dbN(与 Oracle 侧 bind 风格一致; MySqlClient 内芯 text() 原生兼容)
    placeholders = ", ".join(f":db{i}" for i in range(len(databases)))
    params = {f"db{i}": d for i, d in enumerate(databases)}
    sql = (
        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_COMMENT "
        "FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA IN ({placeholders}) AND TABLE_TYPE = :ttype"
    )
    params["ttype"] = table_type
    return querier.query(sql, params)


def _to_rows(raw: list[dict], *, db_alias: str, dataset_type: str) -> list[dict]:
    out = []
    for r in raw:
        # information_schema 列名大写; 兼容驱动可能返小写, 双取
        schema = r.get("TABLE_SCHEMA") or r.get("table_schema") or ""
        name = r.get("TABLE_NAME") or r.get("table_name") or ""
        comment = r.get("TABLE_COMMENT") or r.get("table_comment") or ""
        out.append(dict(
            owner=schema,               # MySQL: owner = database 名(附录 D.3)
            template_name=name,
            db_name=db_alias,           # 实例别名(Oracle 侧此列存 instance 显示名)
            comment=comment,
            dataset_type=dataset_type,
        ))
    return out


def fetch_mysql_table_rows(querier: _Querier, databases: Sequence[str],
                           *, db_alias: str) -> tuple[list[dict], int, int]:
    """拉一个实例的表+视图元数据行(不落库)。返回 (store 行, 表数, 视图数)。

    build 分派层跨多实例累积后一次 replace_metadata(每实例一条连接, 不能合成
    单条 TABLE_SCHEMA IN); 单实例路径由 refresh_mysql_metadata 包一层落库。"""
    base = _fetch_tables(querier, databases, table_type="BASE TABLE")
    views = _fetch_tables(querier, databases, table_type="VIEW")
    rows = (_to_rows(base, db_alias=db_alias, dataset_type="TABLE")
            + _to_rows(views, db_alias=db_alias, dataset_type="VIEW"))
    return rows, len(base), len(views)


def refresh_mysql_metadata(querier: _Querier, engine: Any, *,
                           databases: Sequence[str], db_alias: str,
                           now: str) -> dict[str, Any]:
    """全量快照覆盖刷新 MySQL 表级元数据(单实例路径)。契约与 refresh_metadata 同构。"""
    dbs = [d for d in databases if (d or "").strip()]
    if not dbs:
        return {"refreshed": False, "reason": "no_databases", "tables": 0, "views": 0}
    try:
        rows, n_tab, n_view = fetch_mysql_table_rows(querier, dbs, db_alias=db_alias)
    except Exception as exc:  # noqa: BLE001  断连/超时 -> 保留旧快照
        return {"refreshed": False, "reason": f"{type(exc).__name__}: {exc}",
                "tables": 0, "views": 0}
    # synonym/fk 按能力矩阵为空(MySQL 无 synonym; FK 走 L2 后续增量, 本层不填)。
    # owner_tns=None: 单实例路径不碰 owner_routing(多实例路由在 build 分派层管)。
    store.replace_metadata(engine, tables=rows, synonyms=[], fks=[],
                           owner_tns=None, refreshed_at=now)
    return {"refreshed": True, "tables": n_tab, "views": n_view}
