"""回归: 老 lineage_edges 库(缺 Block 1a 加的列)+ 新代码写带 edge_kind 的边, 不崩。

根因(2026-06-07 真跑坐实): 持久 contextos.db 的 lineage_edges 表建于 Block 1a 之前,
缺 edge_kind / first_seen_at / last_seen_at / is_active / source_fingerprint 列。
store.create_all 旧实现只 metadata.create_all(checkfirst=True), 对已存在表 no-op ->
write_edges 带 edge_kind 时 OperationalError: no column named edge_kind。
store.create_all 改走 ensure_schema(附加式补列)后该路径自愈。

fixture 用中性合成边(无真实客户 schema/owner)。
"""
from __future__ import annotations

from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect, text


def _make_old_lineage_edges(engine):
    """建一张 Block 1a 之前形态的 lineage_edges: 只有 edge_id + 几列, 没 edge_kind 等新列。"""
    old = MetaData()
    Table("lineage_edges", old,
          Column("edge_id", String(32), primary_key=True),
          Column("src_table", String(128)),
          Column("dst_table", String(128)))
    old.create_all(engine)


def test_create_all_migrates_old_lineage_edges_then_write_edges_ok():
    from contextos.lineage import store

    eng = create_engine("sqlite:///:memory:")
    _make_old_lineage_edges(eng)
    assert "edge_kind" not in {c["name"] for c in inspect(eng).get_columns("lineage_edges")}
    # 老库里先塞一条历史边(早于 Block 1a 列)—— 迁移要把它的新列回填成模型默认, 不能留 NULL
    with eng.begin() as c:
        c.execute(text("INSERT INTO lineage_edges (edge_id, src_table, dst_table) "
                       "VALUES ('old1', 'T_OLD', 'T_OLD2')"))

    store.create_all(eng)   # 必须把缺的列补上(原本 no-op)+ 回填既存行

    cols = {c["name"] for c in inspect(eng).get_columns("lineage_edges")}
    assert {"edge_kind", "first_seen_at", "last_seen_at", "is_active"} <= cols

    # 既存历史边的新列被回填成模型标量默认(回归评审 BLOCKER: 否则 NULL 让 clear/读取逻辑踩坑)
    by_id = {r["edge_id"]: r for r in store.all_edges(eng)}
    assert by_id["old1"]["edge_kind"] == "SQL"          # default='SQL', 非 NULL
    assert by_id["old1"]["is_active"] in (True, 1)      # default=True, 非 NULL
    assert by_id["old1"]["first_seen_at"] == ""         # default='', 非 NULL

    # 写带 edge_kind 的对象依赖边 —— 老库下原本 OperationalError
    store.write_edges(eng, [{"edge_id": "x1", "src_table": "T_A", "dst_table": "T_B",
                             "edge_kind": "OBJECT_DEPENDENCY"}])
    rows = store.all_edges(eng)
    assert len(rows) == 2 and by_id["old1"]["edge_kind"] == "SQL"
    assert {r["edge_id"] for r in rows} == {"old1", "x1"}
