# contextos/orchestrator/tests/test_registry.py
from types import SimpleNamespace

from contextos.orchestrator.provider_io import ProviderResult
from contextos.orchestrator.registry import (
    CheapBridge,
    ProviderRegistry,
    RerankBridge,
    build_rag_query,
)


def test_registry_preserves_cheap_order_05_before_06():
    reg = ProviderRegistry()
    reg.register_cheap(CheapBridge("db_lineage_bridge", lambda bd: ProviderResult.miss("db_lineage_bridge", "x")))
    reg.register_cheap(CheapBridge("config_dimension_bridge", lambda bd: ProviderResult.miss("config_dimension_bridge", "x")))
    assert [b.worker_name for b in reg.cheap_bridges] == ["db_lineage_bridge", "config_dimension_bridge"]


def test_register_rerank():
    reg = ProviderRegistry()
    reg.register_rerank(RerankBridge("llm_rerank", lambda bd, c: ProviderResult.miss("llm_rerank", "x")))
    assert reg.rerank_bridge is not None and reg.rerank_bridge.worker_name == "llm_rerank"


def test_build_rag_query_shape():
    bd = SimpleNamespace(key_entities=["PM_OFFER"],
                         matched_capabilities=[SimpleNamespace(capability="billing-charging")],
                         queries=SimpleNamespace(zh="动态计费", en="dynamic charging"))
    q = build_rag_query(bd)
    assert q["key_entities"] == ["PM_OFFER"]
    assert q["matched_capabilities"] == ["billing-charging"]
    assert q["queries"] == {"zh": "动态计费", "en": "dynamic charging"}
    assert q["corpora"] == ["business_docs", "dict_docs"]


def test_build_rag_query_merges_candidate_terms():
    # review R2 HIGH 1:候选表名/键名进 RAG 字面 query(否则 rag 直接桥恒 miss)
    bd = SimpleNamespace(
        key_entities=["动态计费"],
        candidate_table_terms=[SimpleNamespace(term="PM_OFFER")],
        candidate_config_keys=[SimpleNamespace(term="offer.switch")],
        matched_capabilities=[], queries=SimpleNamespace(zh="", en=""))
    q = build_rag_query(bd)
    assert "PM_OFFER" in q["key_entities"]
    assert "offer.switch" in q["key_entities"]
    assert "动态计费" in q["key_entities"]


def test_build_default_registry_wires_d10_method_paths(monkeypatch):
    # review R2 HIGH 2:code_search 文件 -> 05 method_source_paths(无表名也能出 SQL_TABLE)
    from contextos.orchestrator.pipeline import _run_cheap
    from contextos.orchestrator.provider_io import ProviderCandidate
    from contextos.orchestrator.registry import build_default_registry

    captured: dict = {}

    def fake_code(bd, searcher):
        return ProviderResult(worker_name="code_search", score=1.0, candidates=[
            ProviderCandidate(target="com.x.Foo", kind="CLASS",
                              signals={"file": "Foo.java", "name_match_strength": 1.0})])

    def fake_lineage(bd, engine, *, method_source_paths=None):
        captured["paths"] = method_source_paths
        return ProviderResult.miss("db_lineage_bridge", "no_table_match")

    def fake_config(bd, engine):
        return ProviderResult.miss("config_dimension_bridge", "no_entity_match")

    monkeypatch.setattr("contextos.code_intel.code_search.provider.search_code", fake_code)
    monkeypatch.setattr("contextos.lineage.provider.search_lineage", fake_lineage)
    monkeypatch.setattr("contextos.config_dim.provider.search_config", fake_config)

    class FakeRag:
        def search(self, q):
            return ProviderResult.miss("rag", "no_patterns")

    reg = build_default_registry(searcher=object(), rag_provider=FakeRag(),
                                 lineage_engine=object(), config_engine=object(), llm=object())
    bd = SimpleNamespace(assessment="ok", key_entities=[], candidate_table_terms=[],
                         candidate_config_keys=[], matched_capabilities=[],
                         queries=SimpleNamespace(zh="", en=""))
    _run_cheap(reg, bd)
    assert captured["paths"] == ["Foo.java"]            # D10:code_search 文件透传到 05


def test_d10_shared_state_reset_on_registry_reuse(monkeypatch):
    # review R3 MEDIUM 1:registry 复用 + 第二次 code_search 异常 -> 05 收 None,不串第一次的 code_files
    from contextos.orchestrator.pipeline import _run_cheap
    from contextos.orchestrator.provider_io import ProviderCandidate
    from contextos.orchestrator.registry import build_default_registry

    calls = {"n": 0}
    paths_seen: list = []

    def fake_code(bd, searcher):
        calls["n"] += 1
        if calls["n"] == 1:
            return ProviderResult(worker_name="code_search", score=1.0, candidates=[
                ProviderCandidate(target="com.x.Foo", kind="CLASS",
                                  signals={"file": "A.java", "name_match_strength": 1.0})])
        raise RuntimeError("jdt down")                  # 第二次 run:code_search 抛异常

    def fake_lineage(bd, engine, *, method_source_paths=None):
        paths_seen.append(method_source_paths)
        return ProviderResult.miss("db_lineage_bridge", "no_table_match")

    def fake_config(bd, engine):
        return ProviderResult.miss("config_dimension_bridge", "no_entity_match")

    monkeypatch.setattr("contextos.code_intel.code_search.provider.search_code", fake_code)
    monkeypatch.setattr("contextos.lineage.provider.search_lineage", fake_lineage)
    monkeypatch.setattr("contextos.config_dim.provider.search_config", fake_config)

    class FakeRag:
        def search(self, q):
            return ProviderResult.miss("rag", "no_patterns")

    reg = build_default_registry(searcher=object(), rag_provider=FakeRag(),
                                 lineage_engine=object(), config_engine=object(), llm=object())
    bd = SimpleNamespace(assessment="ok", key_entities=[], candidate_table_terms=[],
                         candidate_config_keys=[], matched_capabilities=[],
                         queries=SimpleNamespace(zh="", en=""))
    _run_cheap(reg, bd)        # run 1:code ok -> A.java
    _run_cheap(reg, bd)        # run 2:code 异常 -> 05 应收 None(不串 A.java)
    assert paths_seen == [["A.java"], None]
