# contextos/orchestrator/tests/test_orchestrate_e2e.py
from datetime import datetime

from contextos.orchestrator.pipeline import run_and_persist
from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.orchestrator.registry import CheapBridge, ProviderRegistry, RerankBridge
from contextos.requirement.schema import (
    CandidateConfigKey,
    CandidateName,
    CandidateTableTerm,
    MatchedCapability,
    RequirementBreakdown,
)


def _full_breakdown():
    return RequirementBreakdown(
        requirement_id="req-dyn-charge", raw_text="新增动态计费批量操作,完成后发 SMS 提醒",
        source_kind="text", assessment="ok", confidence=1.0,
        business_intent="动态计费批量操作 + SMS 通知", actions=["add"],
        matched_capabilities=[MatchedCapability(capability="billing-charging", confidence=0.9)],
        candidate_code_names=[CandidateName(term="DynamicCharging", kind="camelcase", source="llm")],
        candidate_table_terms=[CandidateTableTerm(term="PM_OFFER", kind="table_hint", source="llm")],
        candidate_config_keys=[CandidateConfigKey(term="offer.switch", kind="config_key", source="llm")])


def _registry():
    code = [ProviderCandidate(target="com.x.DynamicChargingSVImpl", kind="CLASS",
            signals={"name_match_strength": 1.0, "file": "Dyn.java", "line_start": 5, "line_end": 50})]
    sql = [ProviderCandidate(target="UPC.PM_OFFER", kind="SQL_TABLE",
            signals={"relation_type": "WHERE_EQ", "lineage_type": "DIRECT", "src": None,
                     "dst": {"db": "", "owner": "UPC", "table": "PM_OFFER", "col": None},
                     "evidence_count": 3, "sql_template_id": "t1", "recovery_mode": "literal",
                     "branch_detected": False, "unresolved_reason": None})]
    cfg = [ProviderCandidate(target="offer.switch.enable", kind="CONFIG_KEY",
            signals={"entity_key": "offer.switch.enable", "entity_type": "file_key",
                     "bind_type": "java_class", "bind_strategy": "exact_match", "confidence": "high"})]
    rag = [ProviderCandidate(target="docs/charging.md", kind="BUSINESS_DOC",
            signals={"snippet": "PM_OFFER stores offers; offer.switch.enable toggles it",
                     "rerank_score": 0.9})]

    def rerank_fn(bd, cands):
        out = [ProviderCandidate(target=c.target, kind=c.kind,
               signals={"vote": "support", "status": "ok", "vote_score": 0.7,
                        "relevance": 0.7, "evidence_strength": 0.7}) for c in cands]
        return ProviderResult(worker_name="llm_rerank", score=0.7, candidates=out)

    reg = ProviderRegistry()
    reg.register_cheap(CheapBridge("code_search",
        lambda b: ProviderResult(worker_name="code_search", score=1.0, candidates=code)))
    reg.register_cheap(CheapBridge("rag",
        lambda b: ProviderResult(worker_name="rag", score=0.9, candidates=rag)))
    reg.register_cheap(CheapBridge("db_lineage_bridge",
        lambda b: ProviderResult(worker_name="db_lineage_bridge", score=1.0, candidates=sql)))
    reg.register_cheap(CheapBridge("config_dimension_bridge",
        lambda b: ProviderResult(worker_name="config_dimension_bridge", score=0.8, candidates=cfg)))
    reg.register_rerank(RerankBridge("llm_rerank", rerank_fn))
    return reg


def test_e2e_three_dimensions_and_artifact(tmp_path):
    impact, ctx = run_and_persist(_full_breakdown(), _registry(), raw_input="新增动态计费批量操作",
        artifact_root=tmp_path, now=datetime(2026, 6, 5, 10, 0, 0), short_hash="deadbe")
    by_target = {e.target: e for e in impact.evidence_items}
    assert "com.x.DynamicChargingSVImpl" in by_target
    assert "UPC.PM_OFFER" in by_target
    assert "offer.switch.enable" in by_target
    assert "docs/charging.md" not in by_target          # RAG 文档不进(G5)
    assert impact.dimension_status == {"method": "resolved", "sql_table": "resolved", "config": "resolved"}

    sql_ev = by_target["UPC.PM_OFFER"]
    assert sql_ev.sql_lineage is not None
    assert any(r.source == "rag-cross-encoder" for r in sql_ev.evidence_refs)   # SQL RAG 投影命中
    assert sql_ev.confidence_tier == "HIGH"

    cfg_ev = by_target["offer.switch.enable"]
    assert cfg_ev.config_binding is not None
    assert any(r.source == "rag-cross-encoder" for r in cfg_ev.evidence_refs)   # config RAG 投影命中

    m_ev = by_target["com.x.DynamicChargingSVImpl"]
    assert all(r.source != "rag-cross-encoder" for r in m_ev.evidence_refs)     # method 维不含 RAG

    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "impact_map.json").exists()
    assert (run_dirs[0] / "corroboration.json").exists()
