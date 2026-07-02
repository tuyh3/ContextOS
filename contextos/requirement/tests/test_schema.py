from __future__ import annotations

import pytest
from pydantic import ValidationError

from contextos.requirement.schema import (
    CandidateConfigKey,
    CandidateName,
    CandidateTableTerm,
    DictHits,
    MatchedCapability,
    Queries,
    RequirementBreakdown,
)


def test_minimal_breakdown_only_required_fields():
    """只给三必填字段,其余走默认(空)。"""
    b = RequirementBreakdown(
        requirement_id="req-abc123",
        raw_text="some requirement text",
        source_kind="text",
    )
    assert b.business_intent == ""
    assert b.key_entities == []
    assert b.actions == []
    assert b.matched_capabilities == []
    assert b.candidate_code_names == []
    assert b.candidate_table_terms == []
    assert b.candidate_config_keys == []
    assert isinstance(b.dict_hits, DictHits)
    assert b.dict_hits.interface_dict == []
    assert isinstance(b.queries, Queries)
    assert b.queries.zh == "" and b.queries.en == ""
    assert b.open_questions == []


def test_full_breakdown_from_design_example():
    """design §2 的完整示例(去 jsonc 注释)round-trip 不 raise。"""
    data = {
        "requirement_id": "REQ-DC-001",
        "raw_text": "新增 Dynamic Charging 的 Bulk 操作 + SMS 提醒",
        "source_kind": "docx",
        "business_intent": "新增 Dynamic Charging 的 Bulk 操作(Start/Pause/Stop)+ SMS 提醒",
        "key_entities": ["折扣套餐", "批量启动", "动态计费", "SMS 提醒"],
        "actions": ["add", "modify", "delete"],
        "matched_capabilities": [
            {"capability": "billing-charging", "confidence": 0.92, "evidence": "提及动态计费"},
            {"capability": "product-subscription", "confidence": 0.65, "evidence": "提及套餐订购"},
        ],
        "candidate_code_names": [
            {"term": "DynamicCharging", "kind": "shouty", "source": "regex"},
            {"term": "BulkStart", "kind": "camelcase", "source": "llm"},
            {"term": "Dost", "kind": "proper_noun", "source": "dict-capability"},
        ],
        "candidate_table_terms": [
            {"term": "OFFER", "kind": "entity", "source": "llm"},
            {"term": "PM_OFFER", "kind": "table_hint", "source": "dict-config-table"},
            {"term": "渠道授权", "kind": "business_term", "source": "llm"},
        ],
        "candidate_config_keys": [
            {"term": "offer-permission-switch", "kind": "config_key", "source": "dict-config"},
            {"term": "批量上限", "kind": "param_term", "source": "llm"},
            {"term": "WHITELIST", "kind": "config_table_hint", "source": "llm"},
        ],
        "dict_hits": {
            "interface_dict": [
                {"capability": "代理商业务", "service": "DostService", "source": "能力线-xiongjian"}
            ],
            "capability_line": [],
            "ussd_menu": [],
            "admin_menu": [],
        },
        "queries": {
            "zh": "新增动态计费批量操作功能",
            "en": "Add Bulk operations to Dynamic Charging functionality",
        },
        "open_questions": ["Bulk 上限并发数未提及"],
    }
    b = RequirementBreakdown.model_validate(data)
    assert b.matched_capabilities[0].capability == "billing-charging"
    assert b.candidate_code_names[2].kind == "proper_noun"
    assert b.dict_hits.interface_dict[0].service == "DostService"
    assert b.queries.en.startswith("Add Bulk")


def test_invalid_capability_rejected():
    with pytest.raises(ValidationError):
        MatchedCapability(capability="not-a-capability", confidence=0.5)


def test_confidence_range_enforced():
    with pytest.raises(ValidationError):
        MatchedCapability(capability="billing-charging", confidence=1.5)


def test_invalid_source_kind_rejected():
    with pytest.raises(ValidationError):
        RequirementBreakdown(requirement_id="x", raw_text="y", source_kind="pdf")


def test_candidate_kind_literals_enforced():
    CandidateName(term="X", kind="shouty", source="regex")
    CandidateTableTerm(term="OFFER", kind="entity", source="llm")
    CandidateConfigKey(term="K", kind="config_key", source="llm")
    with pytest.raises(ValidationError):
        CandidateName(term="X", kind="bogus", source="regex")


def test_strict_base_forbids_extra():
    with pytest.raises(ValidationError):
        Queries(zh="a", en="b", fr="c")


def test_breakdown_assessment_confidence_defaults():
    """新增 guard 字段全默认 -> 既有构造方式不破(向后兼容)。"""
    from contextos.requirement.schema import RequirementBreakdown
    b = RequirementBreakdown(requirement_id="r1", raw_text="x", source_kind="text")
    assert b.assessment == "ok"
    assert b.confidence == 1.0


def test_breakdown_assessment_confidence_settable():
    from contextos.requirement.schema import RequirementBreakdown
    b = RequirementBreakdown(
        requirement_id="r1", raw_text="x", source_kind="text",
        assessment="rejected", confidence=0.0,
    )
    assert b.assessment == "rejected"
    assert b.confidence == 0.0


def test_candidate_source_span_default_and_settable():
    from contextos.requirement.schema import (
        CandidateConfigKey, CandidateName, CandidateTableTerm,
    )
    cn = CandidateName(term="Foo", kind="camelcase", source="llm")
    assert cn.source_span == ""
    cn2 = CandidateName(term="Foo", kind="camelcase", source="llm", source_span="新增 Foo")
    assert cn2.source_span == "新增 Foo"
    tt = CandidateTableTerm(term="OFFER", kind="entity", source="llm", source_span="套餐")
    assert tt.source_span == "套餐"
    ck = CandidateConfigKey(term="批量上限", kind="param_term", source="llm", source_span="批量上限")
    assert ck.source_span == "批量上限"


def test_confidence_out_of_range_rejected():
    import pytest
    from pydantic import ValidationError
    from contextos.requirement.schema import RequirementBreakdown
    with pytest.raises(ValidationError):
        RequirementBreakdown(requirement_id="r1", raw_text="x", source_kind="text", confidence=1.5)


def test_candidate_carries_segment_path_default_empty():
    """segment_path 由代码填(候选来自哪段, 供评测/调试); 默认空, 向后兼容。
    三类候选都覆盖, 任意一类 type-change 都能被捕到。
    """
    c = CandidateName(term="X", kind="other", source="llm")
    assert c.segment_path == []
    c2 = CandidateName(term="Y", kind="other", source="llm",
                       segment_path=["账务", "限制逻辑"])
    assert c2.segment_path == ["账务", "限制逻辑"]
    tt = CandidateTableTerm(term="OFFER", kind="entity", source="llm")
    assert tt.segment_path == []
    ck = CandidateConfigKey(term="批量上限", kind="param_term", source="llm")
    assert ck.segment_path == []
