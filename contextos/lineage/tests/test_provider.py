"""db_lineage_bridge provider 测试。"""
from contextos.storage.db import make_engine
from contextos.lineage import store


def _breakdown(**kw):
    from contextos.requirement.schema import RequirementBreakdown
    base = dict(requirement_id="r1", raw_text="x", source_kind="text")
    base.update(kw)
    return RequirementBreakdown.model_validate(base)   # model_validate 收 dict, pyright-clean


def _term(t):
    from contextos.requirement.schema import CandidateTableTerm
    return CandidateTableTerm(term=t, kind="entity", source="llm")


def _eng_offline():
    e = make_engine("sqlite://")
    store.create_all(e)
    store.write_edges(e, [
        dict(edge_id="E1", src_db="", src_owner="", src_table="PM_OFFER_BASE",
             dst_db="", dst_owner="", dst_table="PM_OFFER_CHA",
             relation_type="INSERT_SELECT", lineage_type="DIRECT", confidence="medium",
             evidence_count=3, recovery_mode="sql_file", branch_detected=False),
    ])
    return e


def test_rejected_short_circuits():
    from contextos.lineage.provider import search_lineage
    eng = _eng_offline()
    b = _breakdown(assessment="rejected", confidence=0.0)
    r = search_lineage(b, eng)
    assert r.worker_name == "db_lineage_bridge"
    assert r.miss_reason == "requirement_rejected"


def test_no_terms_is_miss():
    from contextos.lineage.provider import search_lineage
    r = search_lineage(_breakdown(), _eng_offline())
    assert r.miss_reason == "no_table_terms"


def test_term_matches_edge_table():
    from contextos.lineage.provider import search_lineage
    eng = _eng_offline()
    b = _breakdown(candidate_table_terms=[_term("PM_OFFER")])
    r = search_lineage(b, eng)
    assert r.miss_reason is None
    targets = [c.target for c in r.candidates]
    assert any("PM_OFFER_CHA" in t for t in targets)
    c = [c for c in r.candidates if "PM_OFFER_CHA" in c.target][0]
    assert c.kind == "SQL_TABLE"
    sl = c.signals
    assert sl["relation_type"] == "INSERT_SELECT"
    assert sl["recovery_mode"] == "sql_file"
    assert sl["evidence_count"] == 3
    assert sl["unresolved_reason"] is None
    # §14: source_quality(sql_file=1.0) + evidence_corroboration(>=2 -> 1.0) 抬分
    assert 0.0 < r.score <= 1.0
    assert r.score_breakdown["candidate_count"] == float(len(r.candidates))
    assert r.score_breakdown["rag_deferred"] == 1.0      # 03b 未 merge


def test_no_match_is_miss():
    from contextos.lineage.provider import search_lineage
    eng = _eng_offline()
    b = _breakdown(candidate_table_terms=[_term("ZZZ_NOPE")])
    r = search_lineage(b, eng)
    assert r.miss_reason == "no_table_match"
    assert r.candidates == []


def test_method_dataflow_path_adds_tables():
    """给 04 的 method_source_paths -> D10 补表。"""
    from contextos.lineage.provider import search_lineage
    eng = _eng_offline()
    store.write_evidence(eng, [dict(edge_id="E1", evidence_type="CODE_SQL",
                                    evidence_ref="order/PmOfferDao.java:10", excerpt="...")])
    b = _breakdown(candidate_table_terms=[_term("PM_OFFER")])
    r = search_lineage(b, eng, method_source_paths=["order/PmOfferDao.java"])
    assert r.miss_reason is None
    # D10 命中也并进候选(去重)
    assert any("PM_OFFER" in c.target for c in r.candidates)


def test_multi_owner_metadata_no_crash_and_relevance_from_any_owner():
    """同名表跨 owner 元数据: provider 不崩; business_relevance 看任一 owner 的 comment;
    target owner 歧义时留空(不静默挑一个)。回归 review Finding #1 provider 侧。"""
    from contextos.lineage.provider import search_lineage
    eng = make_engine("sqlite://")
    store.create_all(eng)
    store.write_edges(eng, [
        dict(edge_id="E1", src_db="", src_owner="", src_table="COMMON_T",
             dst_db="", dst_owner="", dst_table="PM_OFFER_CHA",
             relation_type="WHERE_EQ", lineage_type="DIRECT", confidence="medium",
             evidence_count=2, recovery_mode="sql_file", branch_detected=False)])
    store.write_table_metadata(eng, [
        dict(template_name="COMMON_T", db_name="DB1", owner="UPC", comment="客户公共表",
             dataset_type="TABLE"),
        dict(template_name="COMMON_T", db_name="DB2", owner="SEC", comment="",
             dataset_type="TABLE")])
    b = _breakdown(candidate_table_terms=[_term("COMMON_T")])
    r = search_lineage(b, eng)                       # 不崩
    assert r.miss_reason is None
    common = [c for c in r.candidates if c.target.endswith("COMMON_T")][0]
    assert common.target == "COMMON_T"               # owner 歧义 -> 不带 owner/db 前缀
    assert r.score_breakdown["business_relevance"] > 0.0   # UPC 有 comment(任一 owner)


def test_target_uses_edge_owner_not_metadata_borrow():
    """候选 target 用边自己已解析的 owner/db, 不借 metadata 别的 owner(review 三轮 HIGH 同类)。

    边是 SEC.T_X(显式 schema), metadata 只有 UPC.T_X -> target 必须 SEC.T_X, 绝不 UPC.T_X。"""
    from contextos.lineage.provider import search_lineage
    eng = make_engine("sqlite://")
    store.create_all(eng)
    store.write_edges(eng, [dict(edge_id="E1", src_db="", src_owner="SEC", src_table="T_X",
        dst_db="", dst_owner="", dst_table="OTHER_T", relation_type="WHERE_EQ",
        lineage_type="DIRECT", confidence="medium", evidence_count=2, recovery_mode="sql_file",
        branch_detected=False)])
    store.write_table_metadata(eng, [dict(template_name="T_X", db_name="DB1", owner="UPC",
        comment="x", dataset_type="TABLE")])
    b = _breakdown(candidate_table_terms=[_term("T_X")])
    r = search_lineage(b, eng)
    tx = [c for c in r.candidates if "T_X" in c.target][0]
    assert tx.target == "SEC.T_X"        # 用边的 SEC, 不借 metadata 的 UPC/DB1


def test_object_dependency_edge_src_object_emits_object_dependency_kind():
    """edge_kind=OBJECT_DEPENDENCY 的边, term 命中 **src 非表对象**(VIEW/PROC)
    -> candidate kind=OBJECT_DEPENDENCY(不是 SQL_TABLE), signals 带 object_dependency 详情。
    回归 Task 3 review Finding #2(分流按『匹配侧 dataset_type』, 不是只看 edge_kind)。"""
    from contextos.lineage.provider import search_lineage
    eng = make_engine("sqlite://")
    store.create_all(eng)
    store.write_edges(eng, [dict(edge_id="OD1", src_db="CCRM3", src_owner="UPC", src_table="V_CUST",
                                 dst_db="CCRM3", dst_owner="UPC", dst_table="CB_CUSTOMER",
                                 relation_type="", lineage_type="DIRECT", src_dataset_type="VIEW",
                                 dst_dataset_type="TABLE", confidence="high", evidence_count=1,
                                 recovery_mode="", branch_detected=False,
                                 edge_kind="OBJECT_DEPENDENCY")])
    # 命中 src 侧的视图名 V_CUST(非表对象)
    b = _breakdown(candidate_table_terms=[_term("V_CUST")])
    res = search_lineage(b, eng)
    od = [c for c in res.candidates if c.kind == "OBJECT_DEPENDENCY"]
    assert od, "命中 src 非表对象(VIEW)应产 kind=OBJECT_DEPENDENCY 候选"
    sig = od[0].signals
    assert sig.get("object_dependency") is not None
    assert sig["object_dependency"]["dep_type"] == "VIEW"          # 匹配侧(src)的 dataset_type
    assert sig["object_dependency"]["src_object"] == "CCRM3.UPC.V_CUST"
    assert sig["object_dependency"]["dst_table"] == "CCRM3.UPC.CB_CUSTOMER"
    assert sig["object_dependency"]["evidence_ref"] == "ALL_DEPENDENCIES"
    # 命中的是 src 视图 V_CUST -> 不应被报成 SQL 表
    assert not [c for c in res.candidates if c.kind == "SQL_TABLE" and "V_CUST" in c.target]


def test_object_dependency_edge_dst_real_table_stays_sql_table():
    """对象依赖边 src=VIEW -> dst=真实 TABLE。需求 term 命中 dst 这张**真表**(dataset_type=TABLE)
    -> 必须留在 SQL_TABLE 维度, 绝不被错分成 OBJECT_DEPENDENCY(否则 assemble 归 OTHER, 真表掉出
    sql_table 维度)。Finding #2 quality important: 分流按匹配侧 dataset_type, 不是只看 edge_kind。"""
    from contextos.lineage.provider import search_lineage
    eng = make_engine("sqlite://")
    store.create_all(eng)
    store.write_edges(eng, [dict(edge_id="OD1", src_db="CCRM3", src_owner="UPC", src_table="V_CUST",
                                 dst_db="CCRM3", dst_owner="UPC", dst_table="CB_CUSTOMER",
                                 relation_type="", lineage_type="DIRECT", src_dataset_type="VIEW",
                                 dst_dataset_type="TABLE", confidence="high", evidence_count=1,
                                 recovery_mode="", branch_detected=False,
                                 edge_kind="OBJECT_DEPENDENCY")])
    # 命中 dst 侧的真实表名 CB_CUSTOMER(dataset_type=TABLE)
    b = _breakdown(candidate_table_terms=[_term("CB_CUSTOMER")])
    res = search_lineage(b, eng)
    cb = [c for c in res.candidates if "CB_CUSTOMER" in c.target]
    assert cb, "CB_CUSTOMER 应命中(对象依赖边 dst 真表)"
    assert all(c.kind == "SQL_TABLE" for c in cb), \
        "对象依赖边的 dst 真表必须留 SQL_TABLE, 不能被压成 OBJECT_DEPENDENCY"
    # CB_CUSTOMER 是真表 -> 绝不产 OBJECT_DEPENDENCY 候选
    assert not [c for c in res.candidates if c.kind == "OBJECT_DEPENDENCY" and "CB_CUSTOMER" in c.target]


def test_procedure_name_term_not_classified_as_sql_table():
    """需求里提到一个 PROCEDURE 名(对象依赖边的 src), 端到端不能被报成 SQL_TABLE。
    Task 3 review Finding #2 精确复现: PRC_BILL(PROCEDURE)-> 不进 SQL 表维度。"""
    from contextos.lineage.provider import search_lineage
    eng = make_engine("sqlite://")
    store.create_all(eng)
    # 对象依赖边: PROCEDURE PRC_BILL -> TABLE CB_BILL(Task 3 把对象名写进 src_table)
    store.write_edges(eng, [dict(edge_id="OD1", src_db="", src_owner="UPC", src_table="PRC_BILL",
                                 dst_db="", dst_owner="UPC", dst_table="CB_BILL",
                                 relation_type="", lineage_type="DIRECT",
                                 src_dataset_type="PROCEDURE", dst_dataset_type="TABLE",
                                 confidence="high", evidence_count=1, recovery_mode="",
                                 branch_detected=False, edge_kind="OBJECT_DEPENDENCY")])
    b = _breakdown(candidate_table_terms=[_term("PRC_BILL")])
    res = search_lineage(b, eng)
    # PRC_BILL 命中(它是对象依赖的 src), 但 kind 必须是 OBJECT_DEPENDENCY 非 SQL_TABLE
    prc = [c for c in res.candidates if "PRC_BILL" in c.target]
    assert prc, "PRC_BILL 应命中(对象依赖边 src)"
    assert all(c.kind == "OBJECT_DEPENDENCY" for c in prc)
    assert not [c for c in res.candidates if c.kind == "SQL_TABLE" and "PRC_BILL" in c.target]


def test_object_dependency_dst_view_attributes_to_matched_object():
    """同类回归(修类不修例): 对象依赖边 src=VIEW -> dst=VIEW(ALL_DEPENDENCIES 允许指向 view)。
    term 命中 dst 视图时, 它仍是非表对象 -> OBJECT_DEPENDENCY, 且 object_dependency 子对象
    归属**匹配侧**(dep_type/src_object 取命中的 dst 视图, 不是固定取 src), dst_table 指另一侧。"""
    from contextos.lineage.provider import search_lineage
    eng = make_engine("sqlite://")
    store.create_all(eng)
    store.write_edges(eng, [dict(edge_id="OD1", src_db="", src_owner="UPC", src_table="V_SRC",
                                 dst_db="", dst_owner="UPC", dst_table="V_DST",
                                 relation_type="", lineage_type="DIRECT",
                                 src_dataset_type="VIEW", dst_dataset_type="VIEW",
                                 confidence="high", evidence_count=1, recovery_mode="",
                                 branch_detected=False, edge_kind="OBJECT_DEPENDENCY")])
    b = _breakdown(candidate_table_terms=[_term("V_DST")])
    res = search_lineage(b, eng)
    dst = [c for c in res.candidates if "V_DST" in c.target]
    assert dst and all(c.kind == "OBJECT_DEPENDENCY" for c in dst), \
        "命中 dst 视图(非表对象)应产 OBJECT_DEPENDENCY"
    od = dst[0].signals["object_dependency"]
    assert od["dep_type"] == "VIEW"
    assert od["src_object"] == "UPC.V_DST"        # 归匹配侧(dst), 非固定 src
    assert od["dst_table"] == "UPC.V_SRC"         # 另一侧
    assert not [c for c in res.candidates if c.kind == "SQL_TABLE"]


def test_same_name_sql_edge_and_object_dependency_both_emitted():
    """probe important 回归: 同一个名字(视图)既被 Java SQL 查询(SQL 边, src_table=视图名,
    解析侧不知是视图 -> dataset_type 空 -> SQL_TABLE 候选), 又出现在 ALL_DEPENDENCIES
    (OBJECT_DEPENDENCY 边, src_dataset_type=VIEW -> OBJECT_DEPENDENCY 候选)。

    两条边都命中同一裸名, 旧实现 matched dict 按裸名去重 + first-write-wins ->
    对象依赖维度被静默丢弃(build 序列里 SQL 边先写, 故 SQL 边赢, object_dependency 详情永不进
    signals)。修复后两个维度并存: 一个 SQL_TABLE 候选 + 一个带 object_dependency 的
    OBJECT_DEPENDENCY 候选, 与最终 corroboration 的 (kind, target) 身份键对齐。"""
    from contextos.lineage.provider import search_lineage
    eng = make_engine("sqlite://")
    store.create_all(eng)
    store.write_edges(eng, [
        # SQL 边: V_REPORT 被 Java SQL 查询; 静态解析不知它是视图 -> dataset_type 留空(当基表)。
        # build 序列里静态 SQL 边先写, 排在对象边前 -> all_edges 先返回它 -> first-write-wins 丢对象边。
        dict(edge_id="SQL1", src_db="", src_owner="UPC", src_table="V_REPORT",
             dst_db="", dst_owner="UPC", dst_table="RPT_OUT",
             relation_type="SELECT", lineage_type="DIRECT", confidence="medium",
             src_dataset_type="", dst_dataset_type="", evidence_count=2,
             recovery_mode="sql_file", branch_detected=False, edge_kind="SQL"),
        # 对象依赖边: 视图 V_REPORT 依赖真表 CB_BASE(ALL_DEPENDENCIES)。
        dict(edge_id="OD1", src_db="", src_owner="UPC", src_table="V_REPORT",
             dst_db="", dst_owner="UPC", dst_table="CB_BASE",
             relation_type="", lineage_type="DIRECT", confidence="high",
             src_dataset_type="VIEW", dst_dataset_type="TABLE", evidence_count=1,
             recovery_mode="", branch_detected=False, edge_kind="OBJECT_DEPENDENCY")])
    b = _breakdown(candidate_table_terms=[_term("V_REPORT")])
    res = search_lineage(b, eng)
    assert res.miss_reason is None
    # SQL_TABLE 维度: V_REPORT 作被查询表
    sql_cands = [c for c in res.candidates if c.kind == "SQL_TABLE" and "V_REPORT" in c.target]
    assert sql_cands, "V_REPORT 被 SQL 查询 -> 必须有 SQL_TABLE 候选"
    # OBJECT_DEPENDENCY 维度: V_REPORT(视图)依赖 CB_BASE 的对象依赖详情, 绝不被静默丢弃
    od_cands = [c for c in res.candidates if c.kind == "OBJECT_DEPENDENCY" and "V_REPORT" in c.target]
    assert od_cands, "V_REPORT 出现在 ALL_DEPENDENCIES -> 必须有 OBJECT_DEPENDENCY 候选(对象依赖维度不丢)"
    od = od_cands[0].signals["object_dependency"]
    assert od["dep_type"] == "VIEW"
    assert od["src_object"] == "UPC.V_REPORT"
    assert od["dst_table"] == "UPC.CB_BASE"
    assert od["evidence_ref"] == "ALL_DEPENDENCIES"


def test_collision_order_independent_object_dependency_first():
    """同名碰撞修复必须与边顺序无关(非确定性回归): 即使对象依赖边排在 SQL 边**之前**返回,
    SQL_TABLE 维度也不能被吞。按 (kind, table) 分桶后两个候选都在, 与行顺序解耦。"""
    from contextos.lineage.provider import search_lineage
    eng = make_engine("sqlite://")
    store.create_all(eng)
    # 故意先写对象依赖边, 再写 SQL 边(模拟另一种返回顺序)
    store.write_edges(eng, [
        dict(edge_id="OD1", src_db="", src_owner="UPC", src_table="V_REPORT",
             dst_db="", dst_owner="UPC", dst_table="CB_BASE",
             relation_type="", lineage_type="DIRECT", confidence="high",
             src_dataset_type="VIEW", dst_dataset_type="TABLE", evidence_count=1,
             recovery_mode="", branch_detected=False, edge_kind="OBJECT_DEPENDENCY"),
        dict(edge_id="SQL1", src_db="", src_owner="UPC", src_table="V_REPORT",
             dst_db="", dst_owner="UPC", dst_table="RPT_OUT",
             relation_type="SELECT", lineage_type="DIRECT", confidence="medium",
             src_dataset_type="", dst_dataset_type="", evidence_count=2,
             recovery_mode="sql_file", branch_detected=False, edge_kind="SQL")])
    b = _breakdown(candidate_table_terms=[_term("V_REPORT")])
    res = search_lineage(b, eng)
    kinds = {c.kind for c in res.candidates if "V_REPORT" in c.target}
    assert kinds == {"SQL_TABLE", "OBJECT_DEPENDENCY"}, \
        f"两维度都应在(与边顺序无关), 实得 {kinds}"


def test_business_relevance_uses_oracle_comment():
    """有 Oracle 元数据 comment -> business_relevance > 0。"""
    from contextos.lineage.provider import search_lineage
    eng = _eng_offline()
    store.write_table_metadata(eng, [dict(template_name="PM_OFFER_CHA", db_name="CCRM3",
                                          owner="UPC", comment="Offer 渠道授权表",
                                          dataset_type="TABLE")])
    b = _breakdown(candidate_table_terms=[_term("PM_OFFER")])
    r = search_lineage(b, eng)
    assert r.score_breakdown["business_relevance"] > 0.0
