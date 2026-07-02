# contextos/orchestrator/tests/test_assemble.py
from types import SimpleNamespace

from contextos.impact_map.schema import ImpactMap
from contextos.orchestrator.assemble import (
    _dimension_quality,
    _map_bind_strategy,
    assemble_impact_map,
    to_evidence_item,
)
from contextos.orchestrator.corroboration import CorroboratedCandidate


def _bd(**kw):
    base = dict(requirement_id="req-1", raw_text="add dynamic charging batch",
                source_kind="text", assessment="ok", confidence=1.0,
                business_intent="动态计费批量操作", actions=["add"],
                matched_capabilities=[], open_questions=[])
    base.update(kw)
    return SimpleNamespace(**base)


def test_method_evidence_no_dimensions():
    cc = CorroboratedCandidate(target="com.x.Foo#bar", kind="METHOD", score_overall=0.9,
        confidence_tier="HIGH", bridge_scores={"code_search": 1.0, "llm_rerank": 0.8},
        consensus_count=2, hit_workers=["code_search", "llm_rerank"],
        signals_by_worker={"code_search": {"name_match_strength": 1.0, "file": "a.java",
                                           "line_start": 10, "line_end": 20},
                           "llm_rerank": {"vote_score": 0.8, "vote": "support", "status": "ok"}},
        rag_score=0.0)
    ev = to_evidence_item(cc, "ev0000", ["add"])
    assert ev.kind == "METHOD"
    assert ev.sql_lineage is None and ev.config_binding is None
    assert ev.change_type == "add_method"
    assert ev.file == "a.java" and ev.line_start == 10
    assert {r.source for r in ev.evidence_refs} == {"jdt-ls-workspaceSymbol", "llm-rerank"}


def test_sql_evidence_g1_g3_coercions():
    cc = CorroboratedCandidate(target="UPC.PM_OFFER", kind="SQL_TABLE", score_overall=0.8,
        confidence_tier="HIGH", bridge_scores={"db_lineage_bridge": 1.0, "rag": 0.0, "llm_rerank": 0.0},
        consensus_count=1, hit_workers=["db_lineage_bridge"],
        signals_by_worker={"db_lineage_bridge": {"relation_type": "WHERE_EQ", "lineage_type": "",
            "src": None, "dst": {"db": "", "owner": "UPC", "table": "PM_OFFER", "col": None},
            "evidence_count": 0, "sql_template_id": None, "recovery_mode": "sql_file",
            "branch_detected": False, "unresolved_reason": None}},
        rag_score=0.0)
    ev = to_evidence_item(cc, "ev0001", ["modify"])
    assert ev.sql_lineage is not None
    assert ev.sql_lineage.evidence_count == 1          # G1: 0 -> floor 1
    assert ev.sql_lineage.lineage_type == "INDIRECT"   # G3: "" -> INDIRECT
    assert ev.sql_lineage.dst.table == "PM_OFFER"
    assert ev.change_type == "db_config_change"


def test_config_evidence_g2_bind_strategy_map():
    cc = CorroboratedCandidate(target="offer.switch.enable", kind="CONFIG_KEY", score_overall=0.7,
        confidence_tier="MEDIUM", bridge_scores={"config_dimension_bridge": 0.8},
        consensus_count=1, hit_workers=["config_dimension_bridge"],
        signals_by_worker={"config_dimension_bridge": {"entity_key": "offer.switch.enable",
            "entity_type": "file_key", "bind_type": "java_class",
            "bind_strategy": "hierarchical_match", "confidence": "high"}},
        rag_score=0.0)
    ev = to_evidence_item(cc, "ev0002", ["modify"])
    assert ev.config_binding is not None
    assert ev.config_binding.bind_strategy == "annotation_prefix_match"   # G2 映射
    assert ev.config_binding.source_type == "file"
    assert ev.config_binding.is_sensitive is False
    assert ev.metadata["raw_bind_strategy"] == "hierarchical_match"       # 原值留 metadata


def test_map_bind_strategy_passthrough_alias_unknown():
    assert _map_bind_strategy("exact_match") == "exact_match"
    assert _map_bind_strategy("xml_id_match") == "annotation_prefix_match"
    assert _map_bind_strategy("totally_unknown") == "ripgrep_fallback"
    assert _map_bind_strategy(None) == "ripgrep_fallback"


def test_evidence_source_provenance_derived():
    # review MEDIUM 4:source 按 recovery_mode / bind_strategy 派生真实 provenance
    from contextos.orchestrator.assemble import _evidence_source
    sb = CorroboratedCandidate(target="UPC.T", kind="SQL_TABLE", score_overall=0.5,
        confidence_tier="MEDIUM", bridge_scores={}, consensus_count=1,
        hit_workers=["db_lineage_bridge"],
        signals_by_worker={"db_lineage_bridge": {"recovery_mode": "string_builder"}}, rag_score=0.0)
    assert _evidence_source("db_lineage_bridge", sb) == "lp-java-extract-builder"
    rg = CorroboratedCandidate(target="k", kind="CONFIG_KEY", score_overall=0.3,
        confidence_tier="MEDIUM", bridge_scores={}, consensus_count=0,
        hit_workers=["config_dimension_bridge"],
        signals_by_worker={"config_dimension_bridge": {"bind_strategy": "ripgrep_fallback"}}, rag_score=0.0)
    assert _evidence_source("config_dimension_bridge", rg) == "ripgrep-config-fallback"
    ct = CorroboratedCandidate(target="UPC.CONF_T", kind="CONFIG_TABLE", score_overall=0.6,
        confidence_tier="MEDIUM", bridge_scores={}, consensus_count=1,
        hit_workers=["config_dimension_bridge"],
        signals_by_worker={"config_dimension_bridge": {"table": "CONF_T", "resolved_owner": "UPC"}},
        rag_score=0.0)
    assert _evidence_source("config_dimension_bridge", ct) == "lp-db-config-marker"


def test_assemble_validates_and_sets_dimension_status():
    cc = CorroboratedCandidate(target="com.x.Foo", kind="CLASS", score_overall=0.9,
        confidence_tier="HIGH", bridge_scores={"code_search": 1.0, "llm_rerank": 0.8},
        consensus_count=2, hit_workers=["code_search", "llm_rerank"],
        signals_by_worker={"code_search": {"name_match_strength": 1.0},
                           "llm_rerank": {"vote_score": 0.8, "vote": "support", "status": "ok"}},
        rag_score=0.0)
    im = assemble_impact_map(_bd(), [cc])
    assert isinstance(im, ImpactMap)                 # 通过 3 个 model_validator(跨字段一致)
    assert im.requirement_id == "req-1"
    assert im.dimension_status["method"] == "resolved"
    assert im.dimension_status["sql_table"] == "not_applicable"
    assert len(im.evidence_items) == 1
    assert im.evidence_items[0].id == "ev0000"


def test_object_dependency_candidate_becomes_other_with_metadata():
    """kind=OBJECT_DEPENDENCY 候选 -> Impact Map item kind=OTHER + metadata.raw_kind +
    metadata.object_dependency 子对象; sql_lineage/config_binding=None(过 schema validator)。
    回归 Task 3 review Finding #2: 对象依赖不污染 SQL 表维度。"""
    cc = CorroboratedCandidate(
        target="CCRM3.UPC.CB_CUSTOMER", kind="OBJECT_DEPENDENCY",
        score_overall=0.8, confidence_tier="HIGH", folded=False, consensus_count=1,
        hit_workers=["db_lineage_bridge"], bridge_scores={"db_lineage_bridge": 0.8},
        rag_score=0.0,
        signals_by_worker={"db_lineage_bridge": {"object_dependency": {
            "dep_type": "VIEW", "src_object": "CCRM3.UPC.V_CUST",
            "dst_table": "CCRM3.UPC.CB_CUSTOMER", "evidence_ref": "ALL_DEPENDENCIES"}}})
    item = to_evidence_item(cc, "ev0001", [])
    assert item.kind == "OTHER"
    assert item.sql_lineage is None
    assert item.config_binding is None
    assert item.metadata["raw_kind"] == "OBJECT_DEPENDENCY"
    assert item.metadata["object_dependency"]["dep_type"] == "VIEW"
    assert item.metadata["object_dependency"]["src_object"] == "CCRM3.UPC.V_CUST"
    assert item.metadata["object_dependency"]["dst_table"] == "CCRM3.UPC.CB_CUSTOMER"
    assert item.metadata["object_dependency"]["evidence_ref"] == "ALL_DEPENDENCIES"


def test_object_dependency_does_not_resolve_sql_dimension():
    """OBJECT_DEPENDENCY 候选不应让 dimension_status.sql_table=resolved
    (它归 OTHER, 不在 KIND_SQL_DIMENSION)。"""
    cc = CorroboratedCandidate(
        target="UPC.PRC_BILL", kind="OBJECT_DEPENDENCY",
        score_overall=0.7, confidence_tier="MEDIUM", consensus_count=1,
        hit_workers=["db_lineage_bridge"], bridge_scores={"db_lineage_bridge": 0.7},
        rag_score=0.0,
        signals_by_worker={"db_lineage_bridge": {"object_dependency": {
            "dep_type": "PROCEDURE", "src_object": "UPC.PRC_BILL",
            "dst_table": "UPC.CB_BILL", "evidence_ref": "ALL_DEPENDENCIES"}}})
    im = assemble_impact_map(_bd(), [cc])
    assert im.dimension_status["sql_table"] == "not_applicable"
    assert im.evidence_items[0].kind == "OTHER"


def test_assemble_empty_corrobs_ok():
    im = assemble_impact_map(_bd(assessment="rejected", business_intent="", raw_text=""), [])
    assert im.evidence_items == []
    assert im.metadata["assessment"] == "rejected"


def _q_cc(kind, *, tier="LOW", consensus=1, hit=None, signals=None):
    return CorroboratedCandidate(
        target="X", kind=kind, score_overall=0.3, confidence_tier=tier,
        bridge_scores={}, consensus_count=consensus,
        hit_workers=hit or [], signals_by_worker=signals or {}, rag_score=0.0)


def test_dimension_quality_config_all_ripgrep_with_llm_ref_is_fallback_only():
    # 关键回归(spec HIGH#2): 候选带 llm-rerank hit 仍按 domain 桥 source 判 -> fallback_only
    cc = _q_cc("CONFIG_KEY", hit=["config_dimension_bridge", "llm_rerank"],
               signals={"config_dimension_bridge": {"bind_strategy": "ripgrep_fallback"},
                        "llm_rerank": {"vote": "support", "status": "ok"}})
    q = _dimension_quality([cc], consensus_min_bridges=2)
    assert q["config"] == "fallback_only"
    assert q["method"] == "not_applicable"
    assert q["sql_table"] == "not_applicable"


def test_dimension_quality_config_partial_fallback_is_low_confidence():
    cc_fb = _q_cc("CONFIG_KEY", hit=["config_dimension_bridge"],
                  signals={"config_dimension_bridge": {"bind_strategy": "ripgrep_fallback"}})
    cc_real = _q_cc("CONFIG_KEY", tier="MEDIUM", consensus=1, hit=["config_dimension_bridge"],
                    signals={"config_dimension_bridge": {"bind_strategy": "exact_match"}})
    q = _dimension_quality([cc_fb, cc_real], consensus_min_bridges=2)
    assert q["config"] == "low_confidence"   # 非全兜底 + 无 HIGH/consensus>=2


def test_dimension_quality_config_with_consensus_is_strong():
    cc = _q_cc("CONFIG_KEY", tier="MEDIUM", consensus=2,
               hit=["config_dimension_bridge", "rag"],
               signals={"config_dimension_bridge": {"bind_strategy": "exact_match"}})
    q = _dimension_quality([cc], consensus_min_bridges=2)
    assert q["config"] == "strong"


def test_dimension_quality_sql_all_low_never_fallback_only():
    cc = _q_cc("SQL_TABLE", hit=["db_lineage_bridge"],
               signals={"db_lineage_bridge": {"recovery_mode": "literal"}})
    q = _dimension_quality([cc], consensus_min_bridges=2)
    assert q["sql_table"] == "low_confidence"   # 空兜底源集 -> 永不 fallback_only


def test_dimension_quality_method_high_is_strong():
    cc = _q_cc("METHOD", tier="HIGH", consensus=2, hit=["code_search"],
               signals={"code_search": {"name_match_strength": 1.0}})
    q = _dimension_quality([cc], consensus_min_bridges=2)
    assert q["method"] == "strong"


def test_assemble_fills_dimension_quality_two_axis_independent():
    from contextos.orchestrator.assemble import assemble_impact_map

    class _BD:
        requirement_id = "r1"
        business_intent = "intent"
        raw_text = "intent"
        matched_capabilities: list = []
        actions: list = []
        open_questions: list = []
        assessment = "ok"
        confidence = 0.9

    cc = _q_cc("CONFIG_KEY", hit=["config_dimension_bridge", "llm_rerank"],
               signals={"config_dimension_bridge": {"bind_strategy": "ripgrep_fallback"},
                        "llm_rerank": {"vote": "support", "status": "ok"}})
    impact = assemble_impact_map(_BD(), [cc], consensus_min_bridges=2)
    # 两轴并存: status 仍按覆盖判 resolved, quality 诚实标 fallback_only
    assert impact.dimension_status["config"] == "resolved"
    assert impact.dimension_quality["config"] == "fallback_only"
    assert impact.dimension_quality["method"] == "not_applicable"
