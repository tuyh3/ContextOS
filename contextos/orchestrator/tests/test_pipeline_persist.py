# contextos/orchestrator/tests/test_pipeline_persist.py
from datetime import datetime

from contextos.orchestrator.pipeline import run_and_persist
from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.orchestrator.registry import CheapBridge, ProviderRegistry, RerankBridge
from contextos.requirement.schema import CandidateName, RequirementBreakdown


def test_run_and_persist_writes_artifact(tmp_path):
    bd = RequirementBreakdown(
        requirement_id="r1", raw_text="add Foo", source_kind="text",
        business_intent="add foo", actions=["add"],
        candidate_code_names=[CandidateName(term="Foo", kind="camelcase", source="llm")])
    code = [ProviderCandidate(target="com.x.Foo", kind="CLASS", signals={"name_match_strength": 1.0})]
    reg = ProviderRegistry()
    reg.register_cheap(CheapBridge("code_search",
        lambda b: ProviderResult(worker_name="code_search", score=1.0, candidates=code)))
    reg.register_rerank(RerankBridge("llm_rerank", lambda b, c: ProviderResult(
        worker_name="llm_rerank", score=0.8, candidates=[ProviderCandidate(
            target="com.x.Foo", kind="CLASS",
            signals={"vote": "support", "status": "ok", "vote_score": 0.8})])))
    impact, ctx = run_and_persist(bd, reg, raw_input="add Foo", artifact_root=tmp_path,
                                  now=datetime(2026, 5, 29, 14, 30, 52), short_hash="abc123")
    assert len(impact.evidence_items) == 1
    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    assert run_dirs[0].name == "20260529-143052-add-foo-abc123"
    assert (run_dirs[0] / "impact_map.json").exists()
    import json
    summary = json.loads((run_dirs[0] / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "completed"          # §6 全字段(review MEDIUM 3)
    assert summary["total_tokens"] is None
    assert {"started_at", "ended_at", "duration_ms", "version"} <= summary.keys()


def test_run_and_persist_no_artifact_root_skips_write(tmp_path):
    bd = RequirementBreakdown(requirement_id="r1", raw_text="x", source_kind="text")
    reg = ProviderRegistry()
    reg.register_cheap(CheapBridge("code_search", lambda b: ProviderResult.miss("code_search", "x")))
    reg.register_rerank(RerankBridge("llm_rerank", lambda b, c: ProviderResult.miss("llm_rerank", "x")))
    impact, ctx = run_and_persist(bd, reg)              # artifact_root=None -> 不落盘
    assert not (tmp_path / "runs").exists()
