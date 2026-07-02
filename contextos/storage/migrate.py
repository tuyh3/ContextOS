"""附加式 schema 迁移(红线 #6: 走 SQLAlchemy 抽象, 跨 SQLite + 信创 PG 方言)。

根因: 持久 contextos.db 文件跨代码版本存活, `metadata.create_all(checkfirst=True)`
对**已存在**的表是 no-op(只建缺失的表), 模型后加的列在老库里永远缺失 -> 写带该列的
行抛 OperationalError(no such column)。这就是"老库 + 新代码必崩"的通用迁移缺口
(实测 lineage_edges 缺 Block 1a 的 edge_kind / first_seen_at / ... 列)。

ensure_schema 在 create_all 之后, 对每张表 diff 模型列 vs 库内实际列, 给缺失列做:
  1) `ALTER TABLE ... ADD COLUMN`(nullable, 不带 DDL DEFAULT 子句);
  2) 若该列有 Python-side 标量默认, 紧接一条**参数化** UPDATE 把既存行从 NULL 回填成默认。
每列一个独立事务(ADD + 回填同事务原子), 且对 duplicate-column 竞态幂等恢复。

为什么这么设计(对抗评审定稿):
- **只做附加**: 加列 + 回填。不改类型 / 不删列 / 不改约束 —— 那些要真迁移工具(Alembic),
  超出 v1 范围(本项目 schema 演进至今全是加 nullable 列, 见 store.py Block 1a 注)。
- **ADD COLUMN 不带 DDL DEFAULT**: 避开各方言 DEFAULT 字面量语法差异(bool: SQLite 0/1
  vs PG false; 字符串引号)。默认值改由后续**参数化 UPDATE** 回填既存行 + 写入侧
  _normalize_rows 补新行 —— 两端都用列的 Python-side 标量默认, 既存行与新行一致。
  (修评审 BLOCKER: 否则 is_active=True 的既存行会变 NULL, 'if row[is_active]' 当 False 语义反转。)
- **逐列独立事务 + duplicate 幂等恢复**: 两个 init 进程并发时, inspect(读现存列)与 ADD 之间
  有竞态窗口 —— 第二个进程 ADD 同一列会撞 duplicate column。捕获后**重新 inspect** 复核:
  列已在(并发赢家加好了)-> 幂等跳过; 仍不在 -> 真错误(磁盘满/权限)重抛。
  逐列原子也修了"多列单事务部分失败留半截 schema"的评审 BLOCKER。
- 列类型用 `col.type.compile(dialect)` 按方言渲染; 标识符用方言 identifier_preparer quote。
  DDL 里只有模型常量(表名/列名/类型), 无外部输入, 无注入面; 回填值走 bind 参数。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Column, MetaData, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.sql.schema import ColumnDefault


def _scalar_default(column: Column) -> Any:
    """列的 Python-side 标量默认值; 无标量默认(autoincrement PK / 无 default 列)返回 None。

    只取 is_scalar 默认(纯字面量); 可调用 / server_default 不在此回填(让写入侧/DB 处理)。
    与 lineage/store._scalar_default 同语义(此处独立实现, storage 层不反向依赖 lineage)。
    """
    d = column.default
    if isinstance(d, ColumnDefault) and d.is_scalar:
        return d.arg
    return None


def _existing_cols(engine: Engine, table_name: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table_name)}


def ensure_schema(engine: Engine, metadata: MetaData) -> list[str]:
    """建缺失表 + 给已存在表补齐模型里有、库里缺的列(并回填既存行默认)。

    返回新增的 "table.col" 列表(供日志/测试)。幂等; 对并发重复 ADD 安全。
    """
    metadata.create_all(engine, checkfirst=True)   # 缺失的整表先建出来
    preparer = engine.dialect.identifier_preparer
    added: list[str] = []
    for table in metadata.sorted_tables:
        existing = _existing_cols(engine, table.name)
        for col in table.columns:
            if col.name in existing:
                continue
            coltype = col.type.compile(dialect=engine.dialect)
            ddl = (f"ALTER TABLE {preparer.format_table(table)} "
                   f"ADD COLUMN {preparer.format_column(col)} {coltype}")
            dv = _scalar_default(col)
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                    if dv is not None:
                        # 回填既存行: 新列对老行是 NULL, 模型声明了标量默认 -> 补成默认值。
                        # 参数化(values 走 bind), 绕开各方言 DEFAULT 字面量差异。
                        conn.execute(table.update().where(col.is_(None)).values({col.name: dv}))
            except (OperationalError, ProgrammingError):
                # 竞态/重复: 另一进程可能刚加了这列。重新 inspect 复核 —— 已在则幂等跳过, 否则重抛。
                if col.name not in _existing_cols(engine, table.name):
                    raise
                continue
            added.append(f"{table.name}.{col.name}")
    return added
