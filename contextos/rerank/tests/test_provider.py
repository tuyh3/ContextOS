from __future__ import annotations

import json
import threading
import time

from contextos.llm.base import LLMProvider
from contextos.llm.fake import FakeLLM
from contextos.orchestrator.provider_io import ProviderCandidate
from contextos.rerank.provider import rerank
from contextos.rerank.schema import RerankConfig, WORKER_NAME
from contextos.requirement.schema import MatchedCapability, Queries, RequirementBreakdown


def _bd(assessment="ok"):
    # model_validate(dict) 形式:pyright-clean(assessment 形参推断为 str, 直接传构造器会与
    # Literal['ok','degraded','rejected'] 不兼容报错 -- 项目既有约定用 dict 形式规避)。
    return RequirementBreakdown.model_validate({
        "requirement_id": "r1", "raw_text": "x", "source_kind": "text", "assessment": assessment,
        "business_intent": "新增动态计费批量操作",
        "matched_capabilities": [MatchedCapability(capability="billing-charging", confidence=0.9)],
        "queries": Queries(zh="动态计费", en="dynamic charging"),
    })


def _votes(*items):
    """造一个 RerankBatchOutput JSON 串供 FakeLLM 返回。"""
    return json.dumps({"votes": list(items)})


def test_rejected_breakdown_misses():
    res = rerank(_bd(assessment="rejected"),
                 [ProviderCandidate(target="A", kind="METHOD")], FakeLLM(responses=["{}"]))
    assert res.miss_reason == "requirement_rejected" and res.score == 0.0


def test_empty_candidates_misses():
    res = rerank(_bd(), [], FakeLLM(responses=["{}"]))
    assert res.miss_reason == "no_candidates"


def test_support_vote_scores_positive():
    llm = FakeLLM(responses=[_votes(
        {"candidate_index": 0, "vote": "support", "relevance": 0.9,
         "evidence_strength": 0.8, "reasoning": "主实现"})])
    res = rerank(_bd(), [ProviderCandidate(target="DynamicChargingSVImpl#process", kind="METHOD")], llm)
    assert res.worker_name == WORKER_NAME
    c = res.candidates[0]
    assert c.signals["vote"] == "support" and c.signals["status"] == "ok"
    # vote_score = 0.5*0.9 + 0.5*0.8 = 0.85
    assert c.signals["vote_score"] == 0.85
    assert c.signals["dimension_adapter_used"] == "method"


def test_oppose_vote_scores_zero():
    llm = FakeLLM(responses=[_votes(
        {"candidate_index": 0, "vote": "oppose", "relevance": 0.9,
         "evidence_strength": 0.9, "reasoning": "通用工具"})])
    res = rerank(_bd(), [ProviderCandidate(target="CommonLoggingUtil", kind="CLASS")], llm)
    # 关键:oppose 即便 relevance/evidence 高, vote_score 也 = 0(防高分反对反向抬高)
    assert res.candidates[0].signals["vote_score"] == 0.0
    assert res.candidates[0].signals["vote"] == "oppose"


def test_abstain_scores_zero():
    llm = FakeLLM(responses=[_votes(
        {"candidate_index": 0, "vote": "abstain", "relevance": 0.5,
         "evidence_strength": 0.5, "reasoning": "判不准"})])
    res = rerank(_bd(), [ProviderCandidate(target="X", kind="METHOD")], llm)
    assert res.candidates[0].signals["vote_score"] == 0.0


def test_chunk_failure_marks_all_failed_not_oppose():
    # FakeLLM 连返非法 JSON -> structured() 耗尽重试抛 LLMStructuredError -> chunk 全 failed
    llm = FakeLLM(responses=["nope", "nope", "nope"])
    res = rerank(_bd(), [ProviderCandidate(target="X", kind="METHOD")], llm)
    s = res.candidates[0].signals
    assert s["status"] == "failed"        # 运行态
    assert s["vote"] == "abstain"          # 语义票 != oppose(证据缺失不是反对)
    assert s["vote_score"] == 0.0
    assert s["miss_reason"] == "llm_call_failed"


def test_score_breakdown_is_float_only_and_counts():
    llm = FakeLLM(responses=[_votes(
        {"candidate_index": 0, "vote": "support", "relevance": 1.0, "evidence_strength": 1.0},
        {"candidate_index": 1, "vote": "oppose", "relevance": 0.1, "evidence_strength": 0.1})])
    res = rerank(_bd(), [
        ProviderCandidate(target="A", kind="METHOD"),
        ProviderCandidate(target="B", kind="METHOD"),
    ], llm, config=RerankConfig(batch_size=2))
    bd = res.score_breakdown
    assert all(isinstance(v, float) for v in bd.values())   # 纯 dict[str,float]
    assert bd["votes_cast"] == 2.0
    assert bd["votes_support"] == 1.0 and bd["votes_oppose"] == 1.0
    assert bd["method_count"] == 2.0


def test_per_candidate_isolation_batch1():
    # batch=1:第一个候选 LLM 正常, 第二个 chunk 失败 -> 互不连累
    llm = FakeLLM(responses=[
        _votes({"candidate_index": 0, "vote": "support", "relevance": 0.8, "evidence_strength": 0.8}),
        "bad", "bad", "bad",
    ])
    res = rerank(_bd(), [
        ProviderCandidate(target="A", kind="METHOD"),
        ProviderCandidate(target="B", kind="METHOD"),
    ], llm, config=RerankConfig(batch_size=1, max_concurrency=1))  # 串行: 有序 FakeLLM 队列在并发下非确定
    by_t = {c.target: c.signals for c in res.candidates}
    assert by_t["A"]["status"] == "ok" and by_t["A"]["vote"] == "support"
    assert by_t["B"]["status"] == "failed"


def test_defensive_cap_judges_top_n_keeps_rest_skipped():
    llm = FakeLLM(handler=lambda p, s: _votes(
        *[{"candidate_index": i, "vote": "abstain", "relevance": 0.0, "evidence_strength": 0.0}
          for i in range(40)]))
    cands = [ProviderCandidate(target=f"M{i}", kind="METHOD") for i in range(40)]
    res = rerank(_bd(), cands, llm, config=RerankConfig(method_cap=30, batch_size=40))
    assert res.score_breakdown["method_count"] == 30.0    # 只 LLM 判 top-30
    assert res.score_breakdown["votes_skipped"] == 10.0   # 其余 10 标 skipped
    assert len(res.candidates) == 40                       # 一条不少(可 audit)


def test_over_cap_preserved_as_skipped():
    """over-cap 候选不静默丢:标 status=skipped / miss_reason=cap_skipped 保留。"""
    llm = FakeLLM(handler=lambda p, s: _votes(
        {"candidate_index": 0, "vote": "support", "relevance": 0.8, "evidence_strength": 0.8}))
    cands = [ProviderCandidate(target=f"M{i}", kind="METHOD") for i in range(2)]
    res = rerank(_bd(), cands, llm, config=RerankConfig(method_cap=1, batch_size=1))
    by_t = {c.target: c.signals for c in res.candidates}
    assert len(res.candidates) == 2
    assert by_t["M0"]["status"] == "ok"
    assert by_t["M1"]["status"] == "skipped"
    assert by_t["M1"]["miss_reason"] == "cap_skipped"
    assert by_t["M1"]["vote"] == "abstain" and by_t["M1"]["vote_score"] == 0.0


def test_unknown_kind_skipped_not_routed_to_method():
    """v2 占位 / 未知 kind 不硬塞 method, 标 skipped 且不调 LLM。"""
    llm = FakeLLM(responses=["should-not-be-called"])
    res = rerank(_bd(), [ProviderCandidate(target="menu/x", kind="MENU")], llm)
    s = res.candidates[0].signals
    assert s["status"] == "skipped" and s["miss_reason"] == "unsupported_kind"
    assert s["dimension_adapter_used"] == "unsupported"   # 没当 method 判
    assert llm.calls == []                                 # 没调 LLM
    assert res.score_breakdown["votes_skipped"] == 1.0


def test_provider_score_includes_skipped_in_mean():
    """契约 §4.2: provider score = 逐候选 vote_score 均值, 含 skipped 的 0(不是只算 ok)。"""
    llm = FakeLLM(handler=lambda p, s: _votes(
        {"candidate_index": 0, "vote": "support", "relevance": 1.0, "evidence_strength": 1.0}))
    cands = [ProviderCandidate(target=f"M{i}", kind="METHOD") for i in range(2)]
    res = rerank(_bd(), cands, llm, config=RerankConfig(method_cap=1, batch_size=1))
    # M0 support -> vote_score=1.0; M1 over-cap skipped -> 0.0; 均值 = 0.5(不是只算 M0 的 1.0)
    assert res.score == 0.5


def test_score_breakdown_orthogonality_and_reasoning():
    """最终 review fast-follow: 锁 vote/status 正交分解 + reasoning 真 abstain(此前 0 测试覆盖)。
    1 support + 1 failed: vote 轴 support+oppose+abstain==votes_cast; status 轴 failed 单列;
    reasoning 报真 abstain(扣掉 failed)。"""
    llm = FakeLLM(responses=[
        _votes({"candidate_index": 0, "vote": "support", "relevance": 0.8, "evidence_strength": 0.8}),
        "bad", "bad", "bad"])
    res = rerank(_bd(), [
        ProviderCandidate(target="A", kind="METHOD"),
        ProviderCandidate(target="B", kind="METHOD"),
    ], llm, config=RerankConfig(batch_size=1, max_concurrency=1))  # 串行: 有序 FakeLLM 队列在并发下非确定
    bd = res.score_breakdown
    assert bd["votes_support"] + bd["votes_oppose"] + bd["votes_abstain"] == bd["votes_cast"]
    assert bd["votes_cast"] == 2.0 and bd["votes_support"] == 1.0
    assert bd["votes_abstain"] == 1.0          # B failed 也记 vote=abstain(vote 轴)
    assert bd["votes_failed"] == 1.0 and bd["votes_skipped"] == 0.0   # status 轴正交
    # reasoning 报真 abstain = votes_abstain - failed - skipped = 0, 且带 failed 后缀
    assert "abstain=0" in res.reasoning and "failed=1" in res.reasoning


def test_sql_and_config_caps_route_independently():
    """最终 review fast-follow: 三维 cap 各自独立(此前只 method cap 被对抗测)。"""
    llm = FakeLLM(handler=lambda p, s: _votes(
        *[{"candidate_index": i, "vote": "abstain", "relevance": 0.0, "evidence_strength": 0.0}
          for i in range(30)]))
    sql_cands = [ProviderCandidate(target=f"DB.T{i}", kind="SQL_TABLE") for i in range(25)]
    res = rerank(_bd(), sql_cands, llm, config=RerankConfig(sql_cap=20, batch_size=30))
    assert res.score_breakdown["sql_count"] == 20.0
    assert res.score_breakdown["votes_skipped"] == 5.0
    cfg_cands = [ProviderCandidate(target=f"k{i}", kind="CONFIG_KEY") for i in range(25)]
    res2 = rerank(_bd(), cfg_cands, llm, config=RerankConfig(config_cap=20, batch_size=30))
    assert res2.score_breakdown["config_count"] == 20.0
    assert res2.score_breakdown["votes_skipped"] == 5.0


class _RaisingLookup:
    """非 fail-safe 的 BusinessDocLookup: lookup 抛错(模拟客户/08 传的不守契约实现)。"""

    def lookup(self, candidate, breakdown) -> str:
        raise RuntimeError("rag exploded")


def test_raising_lookup_does_not_crash_run():
    """must-fix(对抗 review): lookup 抛非 fail-safe 异常, 不能崩整轮 -> 降档到无摘要照常投票。"""
    llm = FakeLLM(handler=lambda p, s: _votes(
        {"candidate_index": 0, "vote": "support", "relevance": 0.8, "evidence_strength": 0.8}))
    # sql 维才会调 lookup; 用 SQL_TABLE 候选触发富化路径
    res = rerank(_bd(), [ProviderCandidate(target="T.PM_X", kind="SQL_TABLE")],
                 llm, lookup=_RaisingLookup())
    assert len(res.candidates) == 1                       # 一条不少, 没逃异常崩整轮
    s = res.candidates[0].signals
    assert s["status"] == "ok" and s["vote"] == "support"  # 富化失败 -> 无摘要照常投票


class _NonLLMErrorProvider(LLMProvider):
    """LLMProvider 实现, 其 structured 抛非 LLMError(模拟 host/客户 thinner SDK 抛裸异常)。"""

    def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
        raise ValueError("raw client error")

    def structured(self, prompt, schema, *, system=None, max_retries=2):
        raise ValueError("raw client error")


def test_non_llmerror_marks_chunk_failed_not_crash():
    """must-fix(对抗 review): structured 抛非 LLMError 也要降档 status=failed, 不阻塞 pipeline(§6)。"""
    res = rerank(_bd(), [ProviderCandidate(target="X", kind="METHOD")], _NonLLMErrorProvider())
    assert len(res.candidates) == 1
    s = res.candidates[0].signals
    assert s["status"] == "failed" and s["vote"] == "abstain"
    assert s["miss_reason"] == "llm_call_failed" and s["vote_score"] == 0.0


def test_default_config_batches_to_bound_llm_calls():
    """默认 RerankConfig 必须批量(batch_size>1)。80 候选(30 method+30 sql+20 config, 默认 caps 全判)
    逐候选(batch_size=1)会打 80 次串行 LLM -> 接真 DeepSeek 时实测经常超时。默认批量后 LLM 往返
    应 <=12(batch_size=8: ceil(30/8)+ceil(30/8)+ceil(20/8)=4+4+3=11)。caps 不削以保覆盖。"""
    def handler(prompt, system):
        n = prompt.count("] target=")
        return _votes(*[{"candidate_index": i, "vote": "abstain",
                         "relevance": 0.0, "evidence_strength": 0.0} for i in range(n)])
    llm = FakeLLM(handler=handler)
    cands = (
        [ProviderCandidate(target=f"M{i}", kind="METHOD") for i in range(30)]
        + [ProviderCandidate(target=f"DB.T{i}", kind="SQL_TABLE") for i in range(30)]
        + [ProviderCandidate(target=f"k{i}", kind="CONFIG_KEY") for i in range(20)]
    )
    res = rerank(_bd(), cands, llm)   # 默认 config:不显式传 batch_size/caps
    assert len(llm.calls) <= 12, f"默认逐候选 -> {len(llm.calls)} 次 LLM 往返(应批量到 <=12, 否则超时)"
    assert len(res.candidates) == 80   # 批量不丢候选:80 全判并返回


class _ConcurrencyProbeLLM(LLMProvider):
    """记录最大并发 in-flight 的 LLM 替身: 确定性证明 rerank 真并发跑 chunk(不靠 wall-time)。
    structured() 内部调 complete() 一次, 故在 complete 里记并发 = 记每 chunk 调用的重叠。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

    def complete(self, prompt, *, system=None, temperature=None, max_tokens=None) -> str:
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        time.sleep(0.05)                       # 留窗口让线程重叠(确定性: 串行永远 max=1)
        with self._lock:
            self.in_flight -= 1
        n = prompt.count("] target=")
        return json.dumps({"votes": [
            {"candidate_index": i, "vote": "abstain", "relevance": 0.0, "evidence_strength": 0.0}
            for i in range(n)]})


def test_rerank_runs_chunks_concurrently():
    """07 重排把 ~11 个独立 chunk 调用并发跑(默认 max_concurrency>1), 而非串行(80 候选串行 ~2.8min)。
    chunk 间无共享态, 可并发。串行时 max_in_flight 恒 1; 并发后 >=2(确定性, 因每调用 sleep 留重叠窗口)。"""
    llm = _ConcurrencyProbeLLM()
    cands = (
        [ProviderCandidate(target=f"M{i}", kind="METHOD") for i in range(30)]
        + [ProviderCandidate(target=f"DB.T{i}", kind="SQL_TABLE") for i in range(30)]
        + [ProviderCandidate(target=f"k{i}", kind="CONFIG_KEY") for i in range(20)]
    )
    rerank(_bd(), cands, llm)   # 默认 config: batch_size=8 -> 11 chunks, max_concurrency=6
    assert llm.max_in_flight >= 2, f"chunk 应并发跑, 实测 max in-flight={llm.max_in_flight}(串行=1 即没并发)"
