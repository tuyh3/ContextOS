"""EvidenceItem + EvidenceRef 13 通用字段 + 边界。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contextos.impact_map.evidence import EvidenceItem, EvidenceRef


def _ref(**overrides) -> dict:
    base = {"source": "jdt-ls-workspaceSymbol", "rerank_score": 0.92,
            "content_summary": "DynamicCharging 类符号直接命中"}
    return {**base, **overrides}


def _item(**overrides) -> dict:
    base = {
        "id": "ev-001",
        "target": "order.soa.biz.DynamicChargingSVImpl#batchStart(List<String>)",
        "kind": "METHOD",
        "file": "order/soa/biz/DynamicChargingSVImpl.java",
        "line_start": 120, "line_end": 145,
        "sub_project": "order",
        "business_domain": "订单融合中心",
        "entrypoint_kind": None,
        "change_type": "modify_method",
        "confidence": 0.78,
        "confidence_tier": "HIGH",
        "evidence_refs": [_ref()],
        "reasoning": "多桥共识: 名字命中 + RAG + 调用链 + LLM",
        "miss_reason": None,
    }
    return {**base, **overrides}


def test_minimal_method_evidence_item_parses() -> None:
    item = EvidenceItem(**_item())
    assert item.id == "ev-001"
    assert item.kind == "METHOD"
    assert item.confidence == 0.78
    assert len(item.evidence_refs) == 1
    assert item.evidence_refs[0].source == "jdt-ls-workspaceSymbol"


def test_evidence_ref_content_raw_and_summary_both_optional() -> None:
    ref = EvidenceRef(source="rag-cross-encoder", rerank_score=0.85)
    assert ref.content_raw is None
    assert ref.content_summary is None


def test_confidence_must_be_in_zero_to_one() -> None:
    with pytest.raises(ValidationError):
        EvidenceItem(**_item(confidence=1.5))
    with pytest.raises(ValidationError):
        EvidenceItem(**_item(confidence=-0.1))


def test_rerank_score_must_be_in_zero_to_one() -> None:
    with pytest.raises(ValidationError):
        EvidenceItem(**_item(evidence_refs=[_ref(rerank_score=1.5)]))
    with pytest.raises(ValidationError):
        EvidenceItem(**_item(evidence_refs=[_ref(rerank_score=-0.1)]))


def test_evidence_ref_rejects_typo_field() -> None:
    # EvidenceRef 也是 _StrictBase,extra=forbid 同样生效
    with pytest.raises(ValidationError):
        EvidenceRef(source="rag-cross-encoder", rerank_score=0.5, sorce="typo")


def test_unknown_kind_value_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceItem(**_item(kind="NONSENSE"))


def test_evidence_item_rejects_typo_field() -> None:
    # extra="forbid" 在 _StrictBase 已开
    with pytest.raises(ValidationError):
        EvidenceItem(**_item(targe="typo"))


def test_evidence_item_rejects_empty_evidence_refs() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        EvidenceItem(**_item(evidence_refs=[]))


def test_evidence_ref_source_unknown_value_accepted_open_enum() -> None:
    # source 是开放枚举,unknown 值允许
    ref = EvidenceRef(source="my-custom-provider", rerank_score=0.5)
    assert ref.source == "my-custom-provider"


def test_metadata_default_is_empty_dict() -> None:
    item = EvidenceItem(**_item())
    assert item.metadata == {}
