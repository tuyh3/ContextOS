"""Trip 2 后处理: 06 识别的 config_table 回写 05 lineage_edges.dst_dataset_type(非阻塞)。

依据: design §2 盲区2(06 识别为 config_table 的回写 05 lineage_edges,Trip 2 后处理
非阻塞)+ 构建契约 §3。

只对 05 留的接缝列 dst_dataset_type 做 UPDATE(05 §12.2,默认 TABLE),不碰 05 store.py。
命中 dst_table 的边标 config_table,返回实际改动行数。
"""
from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.engine import Engine

from contextos.lineage import store as L


def writeback_config_tables(engine_05: Engine, config_table_names: set[str]) -> int:
    """把 05 lineage_edges 中 dst_table 命中 config_table_names 的边标 config_table。

    Args:
        engine_05: 05 数据库血缘的 SQLAlchemy Engine(lineage_edges 所在库)。
        config_table_names: 06 识别为配置表的表名集合(裸表名,匹配 dst_table)。

    Returns:
        实际改动的行数(rowcount)。空集合直接返回 0,不发 UPDATE。
    """
    if not config_table_names:
        return 0
    with engine_05.begin() as c:
        res = c.execute(
            update(L.lineage_edges)
            .where(L.lineage_edges.c.dst_table.in_(config_table_names))
            .values(dst_dataset_type="config_table")
        )
        return res.rowcount or 0
