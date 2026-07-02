"""04 输入/信号契约测试。对齐 04 §7。"""
import pytest
from pydantic import ValidationError


def test_search_term_kind_literal():
    from contextos.code_intel.code_search.schema import SearchTerm
    t = SearchTerm(term="DynamicCharging", kind="shouty")
    assert t.term == "DynamicCharging"
    with pytest.raises(ValidationError):
        SearchTerm(term="x", kind="bogus")


def test_code_search_query_defaults():
    from contextos.code_intel.code_search.schema import CodeSearchQuery
    q = CodeSearchQuery()
    assert q.search_terms == []
    assert q.matched_capability == ""
    assert q.sub_project_hints == []


def test_code_search_signals_shape():
    from contextos.code_intel.code_search.schema import CodeSearchSignals
    s = CodeSearchSignals(
        name_match_strength=1.0,
        call_distance_from_seed=0,
        call_direction="seed",
    )
    assert s.binding_source == "jdt-ls"
    assert s.line_start == -1
    d = s.model_dump()
    assert d["name_match_strength"] == 1.0
    assert d["call_direction"] == "seed"


def test_code_search_signals_validates_ranges():
    from contextos.code_intel.code_search.schema import CodeSearchSignals
    with pytest.raises(ValidationError):
        CodeSearchSignals(name_match_strength=1.5, call_distance_from_seed=0, call_direction="seed")
    with pytest.raises(ValidationError):
        CodeSearchSignals(name_match_strength=1.0, call_distance_from_seed=-1, call_direction="seed")
    with pytest.raises(ValidationError):
        CodeSearchSignals(name_match_strength=1.0, call_distance_from_seed=0, call_direction="sideways")
