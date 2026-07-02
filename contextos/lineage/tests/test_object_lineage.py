"""对象血缘: dependencies 表 -> lineage_edges(edge_kind=OBJECT_DEPENDENCY)+ evidence。"""
from contextos.profile.schema import TablesConfig
from contextos.storage.db import make_engine


def _seed(eng):
    from contextos.lineage import store
    store.create_all(eng)
    # 元数据: V_CUST(VIEW) 与 CB_CUSTOMER(TABLE) 都在(NameResolver 解析用)
    store.write_table_metadata(eng, [
        dict(owner="UPC", template_name="CB_CUSTOMER", db_name="CCRM3", comment="客户表",
             dataset_type="TABLE"),
        dict(owner="UPC", template_name="V_CUST", db_name="CCRM3", comment="", dataset_type="VIEW")])
    store.write_dependencies(eng, [
        dict(owner="UPC", name="V_CUST", type="VIEW", referenced_owner="UPC",
             referenced_name="CB_CUSTOMER", referenced_type="TABLE", referenced_link_name="",
             db_name="CCRM3"),
        # proc -> table
        dict(owner="UPC", name="PRC_BILL", type="PROCEDURE", referenced_owner="UPC",
             referenced_name="CB_CUSTOMER", referenced_type="TABLE", referenced_link_name="",
             db_name="CCRM3"),
        # 被过滤: 引用方是 PACKAGE BODY 但被引用方是 SEQUENCE(非 table/view), 不产 view/proc->table 边
        dict(owner="UPC", name="PRC_X", type="PROCEDURE", referenced_owner="UPC",
             referenced_name="SEQ_CUST", referenced_type="SEQUENCE", referenced_link_name="",
             db_name="CCRM3")])


def test_build_object_lineage_produces_high_conf_edges():
    from contextos.lineage import store
    from contextos.lineage.object_lineage import build_object_lineage
    eng = make_engine("sqlite://")
    _seed(eng)
    stats = build_object_lineage(eng, TablesConfig(), now="2026-06-06T00:00:00")
    edges = [e for e in store.all_edges(eng) if e["edge_kind"] == "OBJECT_DEPENDENCY"]
    assert stats["edges"] == 2                         # view->table + proc->table(SEQUENCE 被过滤)
    e = next(x for x in edges if x["src_table"] == "V_CUST")
    assert e["dst_table"] == "CB_CUSTOMER"
    assert e["edge_kind"] == "OBJECT_DEPENDENCY"
    assert e["relation_type"] == ""                    # design §10: 留空, 不套 8 类
    assert e["lineage_type"] == "DIRECT"
    assert e["confidence"] == "high"                   # 系统级, 等同 FK
    assert e["src_dataset_type"] == "VIEW"             # 引用方对象类型落 src_dataset_type
    assert e["dst_dataset_type"] == "TABLE"
    # 证据进 lineage_evidence(evidence_type=OBJECT_DEPENDENCY)
    ev = store.evidence_for(eng, e["edge_id"])
    assert ev and ev[0]["evidence_type"] == "OBJECT_DEPENDENCY"
    assert ev[0]["evidence_ref"] == "ALL_DEPENDENCIES"


def test_view_on_view_dependency_preserves_dst_dataset_type():
    """VIEW-on-VIEW 依赖(Oracle 常见): dst_dataset_type 必须 = 被引用对象真实类型(VIEW),
    不能写死 TABLE。_REFERENCED_TYPES 纳入 VIEW 却把 dst_dataset_type 硬编码 TABLE 自相矛盾,
    会让 Task 4 provider 起步即读到错值。回归 Task 3 review Finding #1。"""
    from contextos.lineage import store
    from contextos.lineage.object_lineage import build_object_lineage
    eng = make_engine("sqlite://")
    store.create_all(eng)
    # V_TOP(VIEW) 建在 V_BASE(VIEW) 之上, 两者都在元数据(dataset_type=VIEW)
    store.write_table_metadata(eng, [
        dict(owner="UPC", template_name="V_TOP", db_name="CCRM3", comment="", dataset_type="VIEW"),
        dict(owner="UPC", template_name="V_BASE", db_name="CCRM3", comment="", dataset_type="VIEW")])
    store.write_dependencies(eng, [
        dict(owner="UPC", name="V_TOP", type="VIEW", referenced_owner="UPC",
             referenced_name="V_BASE", referenced_type="VIEW", referenced_link_name="",
             db_name="CCRM3")])
    build_object_lineage(eng, TablesConfig(), now="2026-06-06T00:00:00")
    edges = [e for e in store.all_edges(eng) if e["edge_kind"] == "OBJECT_DEPENDENCY"]
    e = next(x for x in edges if x["src_table"] == "V_TOP")
    assert e["src_dataset_type"] == "VIEW"
    assert e["dst_dataset_type"] == "VIEW"   # 被引用对象真实类型, 非硬编码 TABLE


def test_dst_dataset_type_falls_back_to_table_when_resolver_blank():
    """离线降级(dst 不在元数据): resolver 返 dataset_type='TABLE' 缺省 -> dst_dataset_type=TABLE。
    保证 Finding #1 修法不破坏离线场景(_dst_dt or 'TABLE')。"""
    from contextos.lineage import store
    from contextos.lineage.object_lineage import build_object_lineage
    eng = make_engine("sqlite://")
    store.create_all(eng)
    # 无元数据(纯离线): resolver._pick_entry 返 dataset_type='TABLE' 缺省
    store.write_dependencies(eng, [
        dict(owner="UPC", name="V_X", type="VIEW", referenced_owner="UPC",
             referenced_name="CB_X", referenced_type="TABLE", referenced_link_name="",
             db_name="CCRM3")])
    build_object_lineage(eng, TablesConfig(), now="2026-06-06T00:00:00")
    edges = [e for e in store.all_edges(eng) if e["edge_kind"] == "OBJECT_DEPENDENCY"]
    e = next(x for x in edges if x["src_table"] == "V_X")
    assert e["dst_dataset_type"] == "TABLE"


def test_build_object_lineage_idempotent_and_keeps_sql_edges():
    from contextos.lineage import store
    from contextos.lineage.object_lineage import build_object_lineage
    eng = make_engine("sqlite://")
    _seed(eng)
    store.write_edges(eng, [dict(edge_id="SQL1", edge_kind="SQL", relation_type="JOIN",
                                 src_table="A", dst_table="B")])
    build_object_lineage(eng, TablesConfig(), now="2026-06-06T00:00:00")
    build_object_lineage(eng, TablesConfig(), now="2026-06-07T00:00:00")   # 二次不翻倍
    obj = [e for e in store.all_edges(eng) if e["edge_kind"] == "OBJECT_DEPENDENCY"]
    sql = [e for e in store.all_edges(eng) if e["edge_kind"] == "SQL"]
    assert len(obj) == 2                               # clear_object_edges 保证幂等
    assert len(sql) == 1 and sql[0]["edge_id"] == "SQL1"   # 静态 SQL 边不受影响


def test_object_dependency_end_to_end_to_impact_map():
    """整链路收口(plan Task 10 Step 2): seed dependencies -> build_object_lineage ->
    search_lineage(term 命中非表对象 V_CUST -> OBJECT_DEPENDENCY 候选)-> corroborate_one
    -> assemble_impact_map -> evidence_item kind=OTHER + metadata.object_dependency, 过 schema validator。

    注: term 用 V_CUST(VIEW, 非基表), 才走 OBJECT_DEPENDENCY 分流(provider Finding #2:
    命中真基表 CB_CUSTOMER 会留 SQL_TABLE, 不归对象依赖)。
    corroborate_one 真签名 = (target, kind, signals_by_worker, rag_proj, cfg);
    assemble 从 signals_by_worker["db_lineage_bridge"]["object_dependency"] 取详情, 故按此键传。
    """
    from contextos.lineage import store
    from contextos.lineage.object_lineage import build_object_lineage
    from contextos.lineage.provider import search_lineage
    from contextos.orchestrator.corroboration import corroborate_one
    from contextos.orchestrator.rag_projection import RagProjection
    from contextos.orchestrator.assemble import assemble_impact_map
    from contextos.profile.schema import CorroborationConfig
    from contextos.requirement.schema import CandidateTableTerm, RequirementBreakdown

    eng = make_engine("sqlite://")
    _seed(eng)
    build_object_lineage(eng, TablesConfig(), now="2026-06-06T00:00:00")

    bd = RequirementBreakdown(
        requirement_id="R1", raw_text="改 V_CUST 视图相关", source_kind="text",
        assessment="ok", confidence=1.0, business_intent="客户视图",
        candidate_table_terms=[CandidateTableTerm(term="V_CUST", kind="entity", source="llm")])
    res = search_lineage(bd, eng)
    od = next(c for c in res.candidates if c.kind == "OBJECT_DEPENDENCY")

    cc = corroborate_one(od.target, od.kind,
                         {"db_lineage_bridge": od.signals},
                         RagProjection([]), CorroborationConfig())
    # OBJECT_DEPENDENCY 不在 EvidenceItem 闭 Kind Literal -> assemble 归一 OTHER + raw_kind 记原值
    im = assemble_impact_map(bd, [cc])               # 过 schema validator 不抛
    item = next(it for it in im.evidence_items
                if it.metadata.get("raw_kind") == "OBJECT_DEPENDENCY")
    assert item.kind == "OTHER"
    assert item.metadata["object_dependency"]["dep_type"] == "VIEW"
    assert item.metadata["object_dependency"]["src_object"].endswith("V_CUST")
    assert item.metadata["object_dependency"]["dst_table"].endswith("CB_CUSTOMER")
    assert item.metadata["object_dependency"]["evidence_ref"] == "ALL_DEPENDENCIES"


def test_object_lineage_crossdb_dblink_edge():
    from contextos.lineage import store
    from contextos.lineage.object_lineage import build_object_lineage
    from contextos.storage.db import make_engine
    from contextos.profile.schema import TablesConfig

    e = make_engine("sqlite://"); store.create_all(e)
    # 本地 view UPC.V_REMOTE 依赖跨库表(经 dblink BILLING)的 CB_BILL
    store.write_dependencies(e, [dict(owner="UPC", name="V_REMOTE", type="VIEW",
                                      referenced_owner="RMT", referenced_name="CB_BILL",
                                      referenced_type="TABLE", referenced_link_name="BILLING",
                                      db_name="CCRM3")])
    # 也 seed table_metadata 让 src_db 有确定值(避免空串让 != 断言恒真通过 -> 无信心)
    store.write_table_metadata(e, [
        dict(owner="UPC", template_name="V_REMOTE", db_name="CCRM3", comment="", dataset_type="VIEW")])
    out = build_object_lineage(e, TablesConfig(), now="2026-06-06T00:00:00",
                               dblink_index={"BILLING": "TEST_DB3"})
    edges = [x for x in store.all_edges(e) if x["edge_kind"] == "OBJECT_DEPENDENCY"]
    assert len(edges) == 1
    assert edges[0]["src_db"] == "CCRM3"            # src 来自本地元数据
    assert edges[0]["dst_db"] == "TEST_DB3"        # dst 来自 dblink_index 解析


def test_object_lineage_unresolvable_dblink_skips_and_registers():
    from contextos.lineage import store
    from contextos.lineage.object_lineage import build_object_lineage
    from contextos.storage.db import make_engine
    from contextos.profile.schema import TablesConfig

    e = make_engine("sqlite://"); store.create_all(e)
    store.write_dependencies(e, [dict(owner="UPC", name="V_REMOTE", type="VIEW",
                                      referenced_owner="RMT", referenced_name="CB_BILL",
                                      referenced_type="TABLE", referenced_link_name="GHOSTLINK",
                                      db_name="CCRM3")])
    build_object_lineage(e, TablesConfig(), now="2026-06-06T00:00:00", dblink_index={})
    assert [x for x in store.all_edges(e) if x["edge_kind"] == "OBJECT_DEPENDENCY"] == []
    assert any(u["db_link"] == "GHOSTLINK" for u in store.all_unresolved_dblinks(e))


def test_build_object_lineage_unresolved_idempotent():
    """build_object_lineage 独立重调: unresolved_dblinks 不应积累重复行(幂等守卫)。

    回归 Task 10 review Issue 2/4: clear_object_edges 只清边, 不清 unresolved_dblinks,
    导致多次调用将 unresolved 行数 1->2->3 增长。修法: 增加
    store.clear_object_unresolved_dblinks 并在 build_object_lineage 开头调。
    """
    from contextos.lineage import store
    from contextos.lineage.object_lineage import build_object_lineage
    from contextos.storage.db import make_engine
    from contextos.profile.schema import TablesConfig

    e = make_engine("sqlite://"); store.create_all(e)
    store.write_dependencies(e, [
        dict(owner="UPC", name="V_REMOTE", type="VIEW",
             referenced_owner="RMT", referenced_name="CB_BILL",
             referenced_type="TABLE", referenced_link_name="GHOSTLINK",
             db_name="CCRM3"),
    ])
    build_object_lineage(e, TablesConfig(), now="N1", dblink_index={})
    build_object_lineage(e, TablesConfig(), now="N2", dblink_index={})
    build_object_lineage(e, TablesConfig(), now="N3", dblink_index={})
    rows = list(store.all_unresolved_dblinks(e))
    assert len(rows) == 1, (
        f"expected 1 unresolved row after 3 rebuilds, got {len(rows)}: {rows}"
    )


def test_build_object_lineage_orfalsy_dblink_regression():
    """or-falsy lookup regression: dbi[full_name]=='' 不能 fallback 到 dbi[base_name]。

    场景: dbi={'BILLING.WORLD': '', 'BILLING': 'TEST_DB3'}, ref_link='BILLING.WORLD'
    -> 应判定为不可解(登记 unresolved, 不产边), 而非错误路由到 TEST_DB3。
    回归 Task 10 review Issue 1: or-falsy 模式 `dbi.get(full) or dbi.get(base)` 将
    空串解读为 falsy, 产生错误的 dst_db='TEST_DB3' 边。
    """
    from contextos.lineage import store
    from contextos.lineage.object_lineage import build_object_lineage
    from contextos.storage.db import make_engine
    from contextos.profile.schema import TablesConfig

    e = make_engine("sqlite://"); store.create_all(e)
    store.write_dependencies(e, [
        dict(owner="UPC", name="V_REMOTE", type="VIEW",
             referenced_owner="RMT", referenced_name="CB_BILL",
             referenced_type="TABLE", referenced_link_name="BILLING.WORLD",
             db_name="CCRM3"),
    ])
    # BILLING.WORLD 映射空串(有意不可解), BILLING 映射真实 TNS
    build_object_lineage(e, TablesConfig(), now="N1",
                         dblink_index={"BILLING.WORLD": "", "BILLING": "TEST_DB3"})
    edges = [x for x in store.all_edges(e) if x["edge_kind"] == "OBJECT_DEPENDENCY"]
    assert edges == [], f"should produce no edge (BILLING.WORLD is intentionally unresolvable), got {edges}"
    unresolved = list(store.all_unresolved_dblinks(e))
    assert any(u["db_link"] == "BILLING.WORLD" for u in unresolved), (
        f"BILLING.WORLD should be registered as unresolved, got {unresolved}"
    )
