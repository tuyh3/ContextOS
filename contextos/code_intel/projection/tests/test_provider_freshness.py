"""provider 路径上的投影语义: 专属诚实 miss + freshness 注入 + live JDT duck-typing 兼容。

真类契约测试(feedback_test_fixtures_match_real_contract): breakdown 用真
RequirementBreakdown 最小合法构造, 不用 SimpleNamespace stub。
"""
from __future__ import annotations

from contextos.code_intel.code_search.provider import search_code
from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection import store
from contextos.code_intel.projection.searcher import ProjectionSearcher
from contextos.requirement.schema import CandidateName, RequirementBreakdown


def _breakdown(term: str) -> RequirementBreakdown:
    """真类最小合法构造: 必填 requirement_id/raw_text/source_kind,
    assessment 默认 ok(非 rejected), candidate_code_names 含该 term。"""
    return RequirementBreakdown(
        requirement_id="req-t11",
        raw_text=f"modify {term} flow",
        source_kind="text",
        candidate_code_names=[CandidateName(term=term, kind="camelcase", source="regex")],
    )


def test_projection_missing_is_honest_miss(engine):
    S.ensure_projection_schema(engine)
    res = search_code(_breakdown("OrderService"), ProjectionSearcher(engine))
    assert res.miss_reason == "code_projection_not_built"
    assert "contextos init" in res.reasoning


def test_freshness_injected_into_candidate_signals(engine):
    S.ensure_projection_schema(engine)
    store.replace_all(engine, {"code_classes": [
        {"class_id": "c1", "class_fqn": "com.acme.OrderService", "class_name": "OrderService",
         "name_lower": "orderservice", "source_file": "src/OS.java"}]})
    store.set_meta(engine, "projection_build_id", "b1")
    store.set_meta(engine, "last_indexed_commit", "c0ffee")
    store.set_meta(engine, "build_status", "ok")
    res = search_code(_breakdown("OrderService"), ProjectionSearcher(engine))
    sig = res.candidates[0].signals
    assert sig["projection_build_id"] == "b1"
    assert sig["indexed_commit"] == "c0ffee"
    assert sig["projection_status"] == "ok"


def test_freshness_failure_does_not_kill_results(engine):
    """LOW-1 回归: freshness 注入失败只丢 freshness(signals 保持默认空串),
    种子候选照常返回, 不升级为整个 provider 崩/miss。"""
    class _Flaky:
        def request_workspace_symbol(self, query):
            return [{"name": "OrderService", "containerName": "com.acme", "kind": 5,
                     "location": {"relativePath": "src/OS.java",
                                  "range": {"start": {"line": 1}, "end": {"line": 2}}}}]

        def freshness(self):
            raise RuntimeError("meta table gone")

    res = search_code(_breakdown("OrderService"), _Flaky())
    assert res.miss_reason is None
    assert len(res.candidates) == 1
    assert res.candidates[0].signals["projection_build_id"] == ""   # 默认空, 未注入


def test_live_jdt_searcher_without_freshness_still_works(engine):
    """duck-typing 兼容: 没有 freshness 方法的 searcher(live JDT adapter)不注入不崩。"""
    class _Fake:
        def request_workspace_symbol(self, query):
            return [{"name": "OrderService", "containerName": "com.acme", "kind": 5,
                     "location": {"relativePath": "src/OS.java",
                                  "range": {"start": {"line": 1}, "end": {"line": 2}}}}]
    res = search_code(_breakdown("OrderService"), _Fake())
    sig = res.candidates[0].signals
    assert sig["projection_build_id"] == ""    # 默认空, 未注入
