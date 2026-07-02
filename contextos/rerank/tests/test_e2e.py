from __future__ import annotations

import json

from contextos.llm.fake import FakeLLM
from contextos.orchestrator.provider_io import ProviderCandidate
from contextos.rerank import RagBusinessDocLookup, rerank
from contextos.rerank.schema import RerankConfig
from contextos.requirement.schema import MatchedCapability, Queries, RequirementBreakdown


def _bd():
    return RequirementBreakdown(
        requirement_id="r1", raw_text="x", source_kind="text",
        business_intent="新增动态计费批量操作 + SMS 提醒",
        key_entities=["计费", "批量"],
        matched_capabilities=[MatchedCapability(capability="billing-charging", confidence=0.9)],
        queries=Queries(zh="动态计费", en="dynamic charging"),
    )


class _FakeRag:
    def search(self, query):
        from contextos.orchestrator.provider_io import ProviderResult
        return ProviderResult(worker_name="rag", score=1.0, candidates=[
            ProviderCandidate(target="doc0", kind="BUSINESS_DOC",
                              signals={"snippet": "PM_OFFER 是套餐主表"})])


class _CredRag:
    """假 RAG: snippet 里带凭据(模拟业务文档万一含口令; 物化期 LeakageGate 应 curate, 07 再兜底)。"""

    def search(self, query):
        from contextos.orchestrator.provider_io import ProviderResult
        return ProviderResult(worker_name="rag", score=1.0, candidates=[
            ProviderCandidate(target="doc0", kind="BUSINESS_DOC", signals={
                "snippet": "连接配置 jdbc.password=supersecret3f7a token=abc123 scott/tiger@db"})])


def test_rag_snippet_credentials_never_reach_llm():
    """§7 07 层兜底端到端: RAG snippet 带凭据, redact 后任何凭据都不进 LLM prompt。"""
    sink = []

    def handler(prompt, system):
        sink.append(prompt)
        return json.dumps({"votes": [
            {"candidate_index": 0, "vote": "abstain", "relevance": 0.0, "evidence_strength": 0.0}]})
    rerank(_bd(), [ProviderCandidate(target="DB.T", kind="SQL_TABLE")],
           FakeLLM(handler=handler), lookup=RagBusinessDocLookup(_CredRag()))
    blob = "\n".join(sink)
    for danger in ("supersecret3f7a", "token=abc123", "tiger@db"):
        assert danger not in blob, f"RAG 凭据进了 prompt: {danger}"


def test_credential_in_target_never_reaches_llm_but_output_keeps_it():
    """§7 07 层兜底: 上游误把凭据塞进 target 也不进 prompt; 但输出 target 保留原值(audit)。"""
    sink = []

    def handler(prompt, system):
        sink.append(prompt)
        return json.dumps({"votes": [
            {"candidate_index": 0, "vote": "abstain", "relevance": 0.0, "evidence_strength": 0.0}]})
    leaky_target = "jdbc:oracle:thin:scott/tiger@db"
    res = rerank(_bd(), [ProviderCandidate(target=leaky_target, kind="CONFIG_KEY",
                                           signals={"entity_key": "k"})],
                 FakeLLM(handler=handler))
    assert "scott/tiger" not in "\n".join(sink)        # prompt 文本里凭据被打码
    assert res.candidates[0].target == leaky_target     # 但输出 target 原样保留(可 audit)


def test_three_dimensions_routed_and_voted():
    # 每维 handler 按候选数返 support 票(简单确定)
    def handler(prompt, system):
        n = prompt.count("] target=")
        return json.dumps({"votes": [
            {"candidate_index": i, "vote": "support", "relevance": 0.7, "evidence_strength": 0.7}
            for i in range(n)]})
    llm = FakeLLM(handler=handler)
    cands = [
        ProviderCandidate(target="DynamicChargingSVImpl#process", kind="METHOD",
                          signals={"name_match_strength": 1.0}),
        ProviderCandidate(target="TESTDB.PM_OFFER", kind="SQL_TABLE",
                          signals={"relation_type": "INSERT_SELECT"}),
        ProviderCandidate(target="offer.switch", kind="CONFIG_KEY",
                          signals={"entity_key": "offer.switch", "bind_strategy": "exact_match"}),
    ]
    res = rerank(_bd(), cands, llm, lookup=RagBusinessDocLookup(_FakeRag()))
    dims = {c.signals["dimension_adapter_used"] for c in res.candidates}
    assert dims == {"method", "sql", "config"}
    assert res.score_breakdown["method_count"] == 1.0
    assert res.score_breakdown["sql_count"] == 1.0
    assert res.score_breakdown["config_count"] == 1.0


def test_sensitive_value_never_reaches_llm():
    """端到端红线:config 候选带 value_raw, 跑完后 LLM 收到的任何 prompt 都不含凭据。"""
    sink = []

    def handler(prompt, system):
        sink.append(prompt)
        return json.dumps({"votes": [
            {"candidate_index": 0, "vote": "abstain", "relevance": 0.0, "evidence_strength": 0.0}]})
    llm = FakeLLM(handler=handler)
    leaky = ProviderCandidate(target="spring.datasource.url", kind="CONFIG_KEY", signals={
        "entity_key": "spring.datasource.url", "bind_strategy": "exact_match",
        "value_raw": "jdbc:oracle:thin:scott/TIGER@db", "value": "secret-token-xyz",
    })
    rerank(_bd(), [leaky], llm)
    all_prompts = "\n".join(sink)
    for danger in ("value_raw", "TIGER", "secret-token-xyz", "scott"):
        assert danger not in all_prompts, f"敏感值进了 prompt: {danger}"


def test_public_api_all_resolves():
    """最终 review fast-follow: __all__ 列的公开符号都真能从包根取到(防漏 re-export)。"""
    import contextos.rerank as r
    for name in r.__all__:
        assert hasattr(r, name), f"missing re-export: {name}"


def test_run_summary_score_is_mean_of_vote_scores():
    def handler(prompt, system):
        return json.dumps({"votes": [
            {"candidate_index": 0, "vote": "support", "relevance": 1.0, "evidence_strength": 1.0},
            {"candidate_index": 1, "vote": "oppose", "relevance": 0.0, "evidence_strength": 0.0}]})
    res = rerank(_bd(), [
        ProviderCandidate(target="A", kind="METHOD"),
        ProviderCandidate(target="B", kind="METHOD"),
    ], FakeLLM(handler=handler), config=RerankConfig(batch_size=2))
    # mean(1.0, 0.0) = 0.5
    assert res.score == 0.5
