"""桥1 provider 入口 search_code 测试。"""
from contextos.code_intel.code_search.tests.test_seeds import FakeSearcher, _sym


def _breakdown(**kw):
    from contextos.requirement.schema import RequirementBreakdown
    base = dict(requirement_id="r1", raw_text="x", source_kind="text")
    base.update(kw)
    return RequirementBreakdown(**base)


def test_search_code_happy_path():
    from contextos.requirement.schema import CandidateName
    from contextos.code_intel.code_search.provider import search_code
    b = _breakdown(candidate_code_names=[
        CandidateName(term="DynamicChargingSVImpl", kind="camelcase", source="llm"),
    ])
    s = FakeSearcher({
        "DynamicChargingSVImpl": [_sym("DynamicChargingSVImpl", 5, "a/D.java", 1, 9,
                                       container="a.b")],
    })
    r = search_code(b, s)
    assert r.worker_name == "code_search"
    assert r.miss_reason is None
    assert len(r.candidates) == 1
    assert r.candidates[0].target == "a.b.DynamicChargingSVImpl"
    # top_name_match=1.0 * source_confidence 0.9
    assert abs(r.score - 0.9) < 1e-9
    assert r.score_breakdown["top_name_match"] == 1.0
    assert r.score_breakdown["source_confidence"] == 0.9
    assert r.score_breakdown["num_seeds"] == 1.0
    assert "1 seed symbol(s)" in r.reasoning


def test_search_code_fuzzy_only_lower_score():
    from contextos.requirement.schema import CandidateName
    from contextos.code_intel.code_search.provider import search_code
    b = _breakdown(candidate_code_names=[CandidateName(term="Charge", kind="other", source="llm")])
    s = FakeSearcher({"Charge": [_sym("DynamicCharging", 5, "a/D.java", 1, 2)]})
    r = search_code(b, s)
    assert abs(r.score - 0.6 * 0.9) < 1e-9
    assert r.score_breakdown["top_name_match"] == 0.6


def test_search_code_no_hits_is_miss():
    from contextos.requirement.schema import CandidateName
    from contextos.code_intel.code_search.provider import search_code
    b = _breakdown(candidate_code_names=[CandidateName(term="Nope", kind="other", source="llm")])
    r = search_code(b, FakeSearcher({}))
    assert r.score == 0.0
    assert r.candidates == []
    assert r.miss_reason == "no_symbol_match"


def test_search_code_no_terms_is_miss():
    from contextos.code_intel.code_search.provider import search_code
    r = search_code(_breakdown(), FakeSearcher({}))
    assert r.miss_reason == "no_search_terms"
    assert r.score == 0.0
    assert r.candidates == []


def test_search_code_rejected_breakdown_short_circuits():
    """02b 判 rejected 的需求 -> 04 直接 miss,不空跑 searcher。"""
    from contextos.code_intel.code_search.provider import search_code

    class _BoomSearcher:
        def request_workspace_symbol(self, query):
            raise AssertionError("searcher must NOT be called for rejected breakdown")

    b = _breakdown(assessment="rejected", confidence=0.0)
    r = search_code(b, _BoomSearcher())
    assert r.miss_reason == "requirement_rejected"


def test_search_code_searcher_exception_is_miss():
    """JDT LS 起不来 / 超时 -> 当 miss(08 §5.1 失败传播),不抛。"""
    from contextos.requirement.schema import CandidateName
    from contextos.code_intel.code_search.provider import search_code

    class _RaisingSearcher:
        def request_workspace_symbol(self, query):
            raise TimeoutError("LSP timeout")

    b = _breakdown(candidate_code_names=[CandidateName(term="Foo", kind="camelcase", source="llm")])
    r = search_code(b, _RaisingSearcher())
    assert r.score == 0.0
    assert r.miss_reason == "jdtls_error"
    assert "TimeoutError" in r.reasoning
    assert "LSP timeout" in r.reasoning
