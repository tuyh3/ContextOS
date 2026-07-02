"""Layer 9 校验去重测试。"""
from contextos.profile.schema import TablesConfig
from contextos.storage.db import make_engine
from contextos.lineage import store


def _resolver(rows=None):
    from contextos.lineage.name_resolve import NameResolver
    e = make_engine("sqlite://")
    store.create_all(e)
    if rows:
        store.write_table_metadata(e, rows)
    return NameResolver(e, TablesConfig())


def test_make_edge_id_undirected_symmetric():
    from contextos.lineage.validate import make_edge_id
    a = make_edge_id("A", "id", "B", "aid", "JOIN")
    b = make_edge_id("B", "aid", "A", "id", "JOIN")
    assert a == b  # JOIN 无向, A<->B 同 id


def test_make_edge_id_directed_keeps_direction():
    from contextos.lineage.validate import make_edge_id
    a = make_edge_id("A", "", "B", "", "INSERT_SELECT")
    b = make_edge_id("B", "", "A", "", "INSERT_SELECT")
    assert a != b  # 有向边保方向


def test_dedup_accumulates_evidence_count():
    from contextos.lineage.validate import deduplicate_edges
    edges = [dict(edge_id="E1", relation_type="JOIN", confidence="low", evidence_count=1),
             dict(edge_id="E1", relation_type="JOIN", confidence="medium", evidence_count=1)]
    evidences = [dict(edge_id="E1"), dict(edge_id="E1")]
    out = deduplicate_edges(edges, evidences)
    assert len(out) == 1
    assert out[0]["evidence_count"] == 2
    assert out[0]["confidence"] == "medium"   # 取最高


def test_validate_offline_keeps_all_edges():
    """离线(无元数据): 不丢边。"""
    from contextos.lineage.validate import validate_edges
    r = _resolver()  # 空元数据
    edges = [dict(edge_id="E1", src_table="UNKNOWN_A", dst_table="UNKNOWN_B",
                  relation_type="JOIN", confidence="low")]
    validated, unknown = validate_edges(edges, r)
    assert len(validated) == 1   # 离线不丢
    assert unknown == []


def test_validate_online_drops_both_unknown():
    """在线(有元数据): src/dst 都不存在 -> 丢, 进 unknown_tables。"""
    from contextos.lineage.validate import validate_edges
    r = _resolver([dict(template_name="KNOWN_T", db_name="D", owner="O",
                        comment="", dataset_type="TABLE")])
    edges = [
        dict(edge_id="E1", src_table="KNOWN_T", dst_table="MISSING", relation_type="JOIN",
             confidence="medium"),
        dict(edge_id="E2", src_table="GHOST_A", dst_table="GHOST_B", relation_type="JOIN",
             confidence="medium"),
    ]
    validated, unknown = validate_edges(edges, r)
    ids = {e["edge_id"] for e in validated}
    assert ids == {"E1"}            # E2 双不存在被丢
    assert "GHOST_A" in unknown and "GHOST_B" in unknown
    # E1 dst 不存在 -> 降 low
    assert [e for e in validated if e["edge_id"] == "E1"][0]["confidence"] == "low"


def test_make_edge_id_distinguishes_owner():
    """同名表跨 owner 的边 id 不同(review HIGH: 显式 schema 身份锚 owner.table)。"""
    from contextos.lineage.validate import make_edge_id
    a = make_edge_id("COMMON_T", "id", "X", "cid", "WHERE_EQ", src_owner="UPC", dst_owner="O")
    b = make_edge_id("COMMON_T", "id", "X", "cid", "WHERE_EQ", src_owner="SEC", dst_owner="O")
    assert a != b
    # 同 owner 同表 -> 同 id(去重不变)
    c = make_edge_id("COMMON_T", "id", "X", "cid", "WHERE_EQ", src_owner="UPC", dst_owner="O")
    assert a == c
