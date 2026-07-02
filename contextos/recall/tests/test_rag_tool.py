"""rag_search tool 适配测试(Plan 10 Task 6)。

tool 形态 = 复用 RagProvider.search,把 (queries, corpora, top_k) 拼成 provider
query dict,返回纯 dict list [{doc, passage, score, corpus}](给 MCP/CLI/lib 共用)。
corpora 校验留 middleware(WF3),本函数假设已校验。FakeRagProvider stub search 返
固定 ProviderResult,避免依赖 ripgrep / 真物化目录。
"""
from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.recall.rag_tool import rag_search


class FakeRagProvider:
    """stub RagProvider.search:记录入参 query,返回 canned ProviderResult。"""

    def __init__(self, result: ProviderResult):
        self._result = result
        self.calls: list[dict] = []

    def search(self, query: dict) -> ProviderResult:
        self.calls.append(query)
        return self._result


def _result(*candidates: ProviderCandidate) -> ProviderResult:
    return ProviderResult(
        worker_name="rag",
        score=candidates[0].signals["rerank_score"] if candidates else 0.0,
        candidates=list(candidates),
    )


def _cand(target: str, snippet: str, score: float) -> ProviderCandidate:
    return ProviderCandidate(
        target=target,
        kind="BUSINESS_DOC",
        signals={"rerank_score": score, "snippet": snippet, "evidence_origin": "text",
                 "lineno": 3, "num_hits": 1},
    )


def test_returns_tool_dict_shape():
    """命中 -> [{doc, passage, score, corpus}] 纯 dict。"""
    fake = FakeRagProvider(_result(
        _cand("app/charge.md", "feature flag config", 0.91),
        _cand("app/notes.md", "secondary hit", 0.40),
    ))
    rows = rag_search(fake, queries={"zh": "计费配置", "en": "charge config"},
                      corpora=["business_docs"])
    assert isinstance(rows, list)
    assert len(rows) == 2
    r = rows[0]
    assert set(r.keys()) == {"doc", "passage", "score", "corpus"}
    assert r["doc"] == "app/charge.md"
    assert r["passage"] == "feature flag config"
    assert r["score"] == 0.91
    # corpus 回填:单 corpus 时标该 corpus(多 corpus 时取传入列表首项作归属标注)
    assert r["corpus"] == "business_docs"
    assert all(isinstance(v, (str, float, int)) for v in r.values())


def test_query_dict_forwarded_to_provider():
    """queries/corpora 正确拼进 provider query dict;key_entities 从 queries 派生(sparse 需要)。"""
    fake = FakeRagProvider(_result(_cand("d.md", "x", 0.5)))
    rag_search(fake, queries={"zh": "动态计费 批量", "en": "dynamic charging"},
               corpora=["business_docs", "ddl_comments"])
    assert len(fake.calls) == 1
    q = fake.calls[0]
    assert q["queries"] == {"zh": "动态计费 批量", "en": "dynamic charging"}
    assert q["corpora"] == ["business_docs", "ddl_comments"]
    # key_entities 非空(sparse 检索靠它;由 queries 文本派生)
    assert isinstance(q["key_entities"], list) and q["key_entities"]


def test_top_k_truncates():
    fake = FakeRagProvider(_result(
        _cand("a.md", "1", 0.9), _cand("b.md", "2", 0.8), _cand("c.md", "3", 0.7),
    ))
    rows = rag_search(fake, queries={"zh": "x", "en": "x"}, corpora=["business_docs"], top_k=2)
    assert len(rows) == 2
    assert [r["doc"] for r in rows] == ["a.md", "b.md"]


def test_miss_returns_empty():
    """provider miss(空候选)-> []，不抛。"""
    fake = FakeRagProvider(ProviderResult.miss("rag", "sparse_no_hits"))
    rows = rag_search(fake, queries={"zh": "zzz", "en": "zzz"}, corpora=["business_docs"])
    assert rows == []


def test_empty_queries_safe():
    """queries 全空 -> 安全返回 []，不打 provider 也不抛。"""
    fake = FakeRagProvider(_result(_cand("d.md", "x", 0.5)))
    rows = rag_search(fake, queries={"zh": "", "en": ""}, corpora=["business_docs"])
    assert rows == []
    assert fake.calls == []  # 无 pattern 可派生 -> 不空打 provider


# --------------------------------------------------------------------------- corpus scoping
# WF3 security finding 修复: corpus_prefixes(profile.config.corpus_subset_prefixes,子集名 ->
# path prefixes)接通 RagProvider path scope(限 grep 范围),provenance 按命中 doc 路径反推真实
# 子集,不伪造成 corpora[0]。


def test_rag_search_scopes_to_corpus_subset(tmp_path):
    """真 RagProvider + 真物化目录: 请求 business_docs 时只搜 business 子目录,不串 secret。"""
    from contextos.recall.rag_provider import RagProvider
    from contextos.recall.reranker.base import Reranker

    (tmp_path / "business").mkdir()
    (tmp_path / "secret").mkdir()
    (tmp_path / "business" / "charge.md").write_text(
        "dynamic charging offer config", encoding="utf-8")
    (tmp_path / "secret" / "private.md").write_text(
        "dynamic charging credential note", encoding="utf-8")

    class _Rer(Reranker):
        def score(self, query: str, passages: list[str]) -> list[float]:
            return [1.0 for _ in passages]

    class _Cfg:
        window_radius = 4
        max_passages_per_doc = 2
        dense_enabled = False

    prov = RagProvider(tmp_path, _Rer(), _Cfg())
    cp = {"business_docs": ["business"], "secret_docs": ["secret"]}
    rows = rag_search(prov, queries={"zh": "dynamic charging", "en": "dynamic charging"},
                      corpora=["business_docs"], corpus_prefixes=cp)
    docs = [r["doc"] for r in rows]
    assert docs, "business 子集应有命中"
    assert any("business" in d for d in docs)
    assert not any("secret" in d for d in docs)              # scope 限: secret 不泄漏
    assert all(r["corpus"] == "business_docs" for r in rows)  # provenance 按 path 反推, 准


def test_rag_search_threads_path_prefixes_and_provenance():
    """corpus_prefixes -> query['path_prefixes'](传给 provider 限范围)+ provenance 按 path 反推。"""
    fake = FakeRagProvider(_result(_cand("business/charge.md", "s", 0.9)))
    cp = {"business_docs": ["business"], "secret_docs": ["secret"]}
    rows = rag_search(fake, queries={"zh": "x", "en": "x"},
                      corpora=["business_docs"], corpus_prefixes=cp)
    assert fake.calls[0]["path_prefixes"] == ["business"]     # scope 传给 provider
    assert rows[0]["corpus"] == "business_docs"               # 按 rel_path 反推真实子集


def test_rag_search_multi_corpus_no_map_does_not_falsify():
    """多 corpus 无映射 -> 无法可靠归属 -> corpus 留空, 不伪造成 corpora[0]。"""
    fake = FakeRagProvider(_result(_cand("x/leak.md", "s", 0.5)))
    rows = rag_search(fake, queries={"zh": "x", "en": "x"},
                      corpora=["business_docs", "dict_docs"])
    assert rows[0]["corpus"] == ""
