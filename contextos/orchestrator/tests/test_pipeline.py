# contextos/orchestrator/tests/test_pipeline.py
from types import SimpleNamespace

from contextos.orchestrator.pipeline import _pool_for_rerank, run_impact_analysis
from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.orchestrator.registry import CheapBridge, ProviderRegistry, RerankBridge


def _bd(assessment="ok"):
    return SimpleNamespace(requirement_id="r1", raw_text="t", source_kind="text",
        assessment=assessment, confidence=1.0, business_intent="bi", actions=["add"],
        matched_capabilities=[], key_entities=["PM_OFFER"],
        queries=SimpleNamespace(zh="z", en="e"), open_questions=[])


def test_pool_dedup_and_sort_by_score_excludes_rag():
    res = {
        "code_search": ProviderResult(worker_name="code_search", score=1.0, candidates=[
            ProviderCandidate(target="A", kind="CLASS", signals={"name_match_strength": 0.6}),
            ProviderCandidate(target="B", kind="CLASS", signals={"name_match_strength": 1.0})]),
        "rag": ProviderResult(worker_name="rag", score=0.9, candidates=[
            ProviderCandidate(target="docs/x.md", kind="BUSINESS_DOC",
                              signals={"snippet": "x", "rerank_score": 0.9})]),
    }
    pool = _pool_for_rerank(res)
    assert [c.target for c in pool] == ["B", "A"]      # 降序 + RAG 排除


def test_pipeline_happy_path_method_high():
    code = [ProviderCandidate(target="com.x.Foo", kind="CLASS", signals={"name_match_strength": 1.0})]

    def rerank_fn(bd, cands):
        out = [ProviderCandidate(target=c.target, kind=c.kind,
               signals={"vote": "support", "status": "ok", "vote_score": 0.8}) for c in cands]
        return ProviderResult(worker_name="llm_rerank", score=0.8, candidates=out)

    reg = ProviderRegistry()
    reg.register_cheap(CheapBridge("code_search",
        lambda bd: ProviderResult(worker_name="code_search", score=1.0, candidates=code)))
    reg.register_rerank(RerankBridge("llm_rerank", rerank_fn))
    im, ctx = run_impact_analysis(_bd(), reg)
    assert len(im.evidence_items) == 1
    assert im.evidence_items[0].target == "com.x.Foo"
    assert im.evidence_items[0].confidence_tier == "HIGH"


def test_pipeline_rejected_short_circuits():
    reg = ProviderRegistry()
    reg.register_cheap(CheapBridge("code_search", lambda bd: ProviderResult.miss("code_search", "x")))
    reg.register_rerank(RerankBridge("llm_rerank", lambda bd, c: ProviderResult.miss("llm_rerank", "x")))
    im, ctx = run_impact_analysis(_bd(assessment="rejected"), reg)
    assert im.evidence_items == []
    assert ctx["cheap_results"] == {}                  # rejected -> 没跑桥


def test_pipeline_bridge_exception_becomes_miss():
    def boom(bd):
        raise RuntimeError("jdt down")
    reg = ProviderRegistry()
    reg.register_cheap(CheapBridge("code_search", boom))
    reg.register_rerank(RerankBridge("llm_rerank", lambda bd, c: ProviderResult.miss("llm_rerank", "no_candidates")))
    im, ctx = run_impact_analysis(_bd(), reg)
    assert ctx["cheap_results"]["code_search"].miss_reason.startswith("bridge_error:RuntimeError")
    assert im.evidence_items == []


def test_pipeline_sql_rag_projection_and_no_doc_candidate():
    sql = [ProviderCandidate(target="UPC.PM_OFFER", kind="SQL_TABLE",
        signals={"recovery_mode": "literal", "evidence_count": 2, "relation_type": "WHERE_EQ",
                 "lineage_type": "DIRECT", "src": None,
                 "dst": {"db": "", "owner": "UPC", "table": "PM_OFFER", "col": None},
                 "branch_detected": False, "sql_template_id": None, "unresolved_reason": None})]
    rag = [ProviderCandidate(target="docs/spec.md", kind="BUSINESS_DOC",
        signals={"snippet": "table PM_OFFER holds offers", "rerank_score": 0.9})]
    reg = ProviderRegistry()
    reg.register_cheap(CheapBridge("db_lineage_bridge",
        lambda bd: ProviderResult(worker_name="db_lineage_bridge", score=1.0, candidates=sql)))
    reg.register_cheap(CheapBridge("rag",
        lambda bd: ProviderResult(worker_name="rag", score=0.9, candidates=rag)))
    reg.register_rerank(RerankBridge("llm_rerank", lambda bd, c: ProviderResult.miss("llm_rerank", "no_candidates")))
    im, ctx = run_impact_analysis(_bd(), reg)
    targets = {e.target for e in im.evidence_items}
    assert "docs/spec.md" not in targets               # RAG 文档不当候选(G5)
    assert "UPC.PM_OFFER" in targets
    ev = next(e for e in im.evidence_items if e.target == "UPC.PM_OFFER")
    assert any(r.source == "rag-cross-encoder" for r in ev.evidence_refs)   # RAG 投影命中 -> 直接桥
    assert ev.sql_lineage is not None


def test_pipeline_folded_candidate_retained_with_flag():
    # review HIGH 2:LOW + llm oppose -> folded,但仍保留在 evidence_items(recall 一条不丢)
    code = [ProviderCandidate(target="com.x.Junk", kind="CLASS", signals={"name_match_strength": 0.3})]

    def rerank_fn(bd, cands):
        return ProviderResult(worker_name="llm_rerank", score=0.0, candidates=[
            ProviderCandidate(target=c.target, kind=c.kind,
                signals={"vote": "oppose", "status": "ok", "vote_score": 0.0}) for c in cands])

    reg = ProviderRegistry()
    reg.register_cheap(CheapBridge("code_search",
        lambda b: ProviderResult(worker_name="code_search", score=0.3, candidates=code)))
    reg.register_rerank(RerankBridge("llm_rerank", rerank_fn))
    im, ctx = run_impact_analysis(_bd(), reg)
    ev = next(e for e in im.evidence_items if e.target == "com.x.Junk")
    assert ev.confidence_tier == "LOW"
    assert ev.metadata["folded"] is True
