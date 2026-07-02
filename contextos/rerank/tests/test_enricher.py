from __future__ import annotations

from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.rerank.enricher import (
    BusinessDocLookup, NullLookup, RagBusinessDocLookup,
)
from contextos.requirement.schema import (
    MatchedCapability, Queries, RequirementBreakdown,
)


def _breakdown():
    return RequirementBreakdown(
        requirement_id="r1", raw_text="x", source_kind="text",
        business_intent="新增套餐订购",
        key_entities=["套餐", "订购"],
        matched_capabilities=[MatchedCapability(capability="product-subscription", confidence=0.9)],
        queries=Queries(zh="套餐订购", en="offer subscribe"),
    )


def test_null_lookup_returns_empty():
    out = NullLookup().lookup(ProviderCandidate(target="X", kind="METHOD"), _breakdown())
    assert out == ""


def test_null_lookup_satisfies_protocol():
    assert isinstance(NullLookup(), BusinessDocLookup)


class _FakeRag:
    """假 RagProvider: 记录 query, 返预置 snippet 候选。"""

    def __init__(self, snippets):
        self._snippets = snippets
        self.last_query = None

    def search(self, query: dict) -> ProviderResult:
        self.last_query = query
        cands = [ProviderCandidate(target=f"doc{i}", kind="BUSINESS_DOC",
                                   signals={"snippet": s}) for i, s in enumerate(self._snippets)]
        return ProviderResult(worker_name="rag", score=1.0, candidates=cands)


def test_rag_lookup_concats_top_snippets():
    # 中性合成表名(TESTDB/APP, 非真客户 schema/db; 守 feedback_offline_test_neutral_fixtures)
    rag = _FakeRag(["套餐表 PM_OFFER 存套餐主数据", "订购流水在 ORDER 表", "第三条", "第四条"])
    enr = RagBusinessDocLookup(rag, top_k=3, max_chars=500)
    out = enr.lookup(ProviderCandidate(target="TESTDB.PM_OFFER", kind="SQL_TABLE"), _breakdown())
    assert "PM_OFFER 存套餐主数据" in out
    assert "第四条" not in out                 # 只取 top_k=3


def test_rag_lookup_builds_query_from_candidate_and_breakdown():
    rag = _FakeRag(["x"])
    enr = RagBusinessDocLookup(rag)
    enr.lookup(ProviderCandidate(target="TESTDB.APP.PM_OFFER_CHA", kind="SQL_TABLE"), _breakdown())
    q = rag.last_query
    assert q is not None and "key_entities" in q     # narrow Optional + 断结构(非空判)
    assert "PM_OFFER_CHA" in q["key_entities"]       # 候选名末段
    assert "套餐" in q["key_entities"]                # + breakdown 业务词
    assert q["queries"]["zh"] == "套餐订购"
    assert q["corpora"] == ["business_docs", "dict_docs"]   # 03 §10 契约字段(07 是声明调用方)


def test_rag_lookup_query_name_tail_for_method_and_config_shapes():
    """钉死候选名末段提取: METHOD 的 '#' 形 / CONFIG 的点分形, 防 split 顺序被改坏。"""
    rag = _FakeRag(["x"])
    enr = RagBusinessDocLookup(rag)
    # METHOD: 'Owner#method' -> 末段取方法名
    enr.lookup(ProviderCandidate(target="SampleSvcImpl#doProcess", kind="METHOD"), _breakdown())
    q1 = rag.last_query
    assert q1 is not None and "doProcess" in q1["key_entities"]
    # CONFIG: 点分 key -> 末段(MVP 已知局限: 末段偏泛, 见 plan known-reality #3, fail-safe 兜底)
    enr.lookup(ProviderCandidate(target="offer-permission-switch.enable", kind="CONFIG_KEY"), _breakdown())
    q2 = rag.last_query
    assert q2 is not None and "enable" in q2["key_entities"]


def test_rag_lookup_filters_empty_snippets():
    # 空 snippet 被 `if s` 过滤掉, 不产生空行/双换行
    rag = _FakeRag(["first", "", "third"])
    out = RagBusinessDocLookup(rag, top_k=3).lookup(
        ProviderCandidate(target="X", kind="SQL_TABLE"), _breakdown())
    assert out == "first\nthird"
    assert "\n\n" not in out


def test_rag_lookup_failsafe_on_exception():
    class _Boom:
        def search(self, query):
            raise RuntimeError("rag down")
    out = RagBusinessDocLookup(_Boom()).lookup(
        ProviderCandidate(target="X", kind="SQL_TABLE"), _breakdown())
    assert out == ""                            # RAG 挂了不阻塞, 返空摘要


def test_rag_lookup_truncates_to_max_chars():
    rag = _FakeRag(["A" * 1000])
    out = RagBusinessDocLookup(rag, max_chars=50).lookup(
        ProviderCandidate(target="X", kind="CONFIG_KEY"), _breakdown())
    assert len(out) <= 50
