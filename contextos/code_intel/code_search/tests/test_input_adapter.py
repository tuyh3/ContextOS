"""02 RequirementBreakdown -> 04 CodeSearchQuery 归一测试(README §5 契约 02->04)。"""


def _breakdown(**kw):
    from contextos.requirement.schema import RequirementBreakdown
    base = dict(requirement_id="r1", raw_text="x", source_kind="text")
    base.update(kw)
    return RequirementBreakdown(**base)


def test_maps_candidate_code_names_to_terms():
    from contextos.requirement.schema import CandidateName
    from contextos.code_intel.code_search.input_adapter import breakdown_to_query
    b = _breakdown(candidate_code_names=[
        CandidateName(term="DynamicCharging", kind="shouty", source="llm"),
        CandidateName(term="BulkStart", kind="camelcase", source="regex"),
    ])
    q = breakdown_to_query(b)
    assert [(t.term, t.kind) for t in q.search_terms] == [
        ("DynamicCharging", "shouty"), ("BulkStart", "camelcase")
    ]


def test_picks_highest_confidence_capability():
    from contextos.requirement.schema import MatchedCapability
    from contextos.code_intel.code_search.input_adapter import breakdown_to_query
    b = _breakdown(matched_capabilities=[
        MatchedCapability(capability="notification", confidence=0.3),
        MatchedCapability(capability="billing-charging", confidence=0.9),
    ])
    q = breakdown_to_query(b)
    assert q.matched_capability == "billing-charging"


def test_filters_blank_terms_and_empty_capability():
    from contextos.requirement.schema import CandidateName
    from contextos.code_intel.code_search.input_adapter import breakdown_to_query
    b = _breakdown(candidate_code_names=[
        CandidateName(term="  ", kind="other", source="llm"),
        CandidateName(term="Foo", kind="camelcase", source="llm"),
    ])
    q = breakdown_to_query(b)
    assert [t.term for t in q.search_terms] == ["Foo"]
    assert q.matched_capability == ""
    assert q.sub_project_hints == []
