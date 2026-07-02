"""顶层 ImpactMap 模型 + 跨字段一致性 validator + JSON round-trip。

替换 POC dataclass 测试 — 旧 EvidenceItem dataclass 在 Task 4 后只走 schema.py
向后兼容 re-export 路径。
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from contextos.impact_map.schema import ImpactMap, Relation


def _evidence_item_method(**overrides) -> dict:
    base = {
        "id": "ev-001",
        "target": "order.DynamicChargingSVImpl#batchStart",
        "kind": "METHOD",
        "change_type": "modify_method",
        "confidence": 0.78,
        "confidence_tier": "HIGH",
        "evidence_refs": [{"source": "jdt-ls-workspaceSymbol", "rerank_score": 0.92}],
    }
    return {**base, **overrides}


def _evidence_item_sql_table(**overrides) -> dict:
    base = {
        "id": "ev-sql-001",
        "target": "CCRM3.UPC.PM_OFFER_CHA",
        "kind": "SQL_TABLE",
        "change_type": "db_config_change",
        "confidence": 0.85,
        "confidence_tier": "HIGH",
        "evidence_refs": [{"source": "lp-sql-recover-literal", "rerank_score": 0.85}],
        "sql_lineage": {
            "relation_type": "INSERT_SELECT",
            "lineage_type": "DIRECT",
            "dst": {"db": "CCRM3", "owner": "UPC", "table": "PM_OFFER_CHA"},
            "evidence_count": 3,
            "recovery_mode": "string_builder",
        },
    }
    return {**base, **overrides}


def _evidence_item_config_key(**overrides) -> dict:
    base = {
        "id": "ev-cfg-001",
        "target": "spring.datasource.url",
        "kind": "CONFIG_KEY",
        "change_type": "config_change",
        "confidence": 0.72,
        "confidence_tier": "MEDIUM",
        "evidence_refs": [{"source": "lp-config-parser-properties", "rerank_score": 0.7}],
        "config_binding": {
            "entity_type": "file_key",
            "source_type": "file",
            "bind_type": "java_method",
            "bind_direction": "read",
            "bind_strategy": "exact_match",
        },
    }
    return {**base, **overrides}


def _impact_map(**overrides) -> dict:
    base = {
        "requirement_id": "REQ-2026-001",
        "requirement_summary": "动态计费 bulk start 支持月套餐",
        "version": "1.0",
        "matched_business_capabilities": [
            {"capability": "billing-charging", "confidence": 0.92}
        ],
        "candidate_entrypoints": [
            {"kind": "API", "target": "order.IDynamicChargingCSV.batchStart"}
        ],
        "modules_touched": [
            {"sub_project": "order", "business_domain": "订单融合中心"}
        ],
        "dimension_status": {
            "method": "resolved",
            "sql_table": "resolved",
            "config": "resolved",
        },
        "known_limitations": [],
        "evidence_items": [_evidence_item_method()],
        "relations": [],
        "open_questions": [],
        "metadata": {},
    }
    return {**base, **overrides}


def test_minimal_method_only_impact_map_parses() -> None:
    m = ImpactMap(**_impact_map())
    assert m.requirement_id == "REQ-2026-001"
    assert len(m.evidence_items) == 1
    assert m.evidence_items[0].kind == "METHOD"


def test_three_dimension_impact_map_parses() -> None:
    m = ImpactMap(**_impact_map(evidence_items=[
        _evidence_item_method(),
        _evidence_item_sql_table(),
        _evidence_item_config_key(),
    ]))
    assert {it.kind for it in m.evidence_items} == {"METHOD", "SQL_TABLE", "CONFIG_KEY"}


def test_method_kind_must_not_have_sql_lineage() -> None:
    with pytest.raises(ValidationError, match="METHOD.*should not have sql_lineage"):
        ImpactMap(**_impact_map(evidence_items=[
            _evidence_item_method(sql_lineage={
                "relation_type": "WHERE_EQ", "lineage_type": "DIRECT",
                "dst": {"db": "X", "owner": "X", "table": "T"},
                "evidence_count": 1, "recovery_mode": "literal",
            }),
        ]))


def test_sql_kind_requires_sql_lineage() -> None:
    item = _evidence_item_sql_table()
    del item["sql_lineage"]
    with pytest.raises(ValidationError, match="SQL_TABLE.*requires sql_lineage"):
        ImpactMap(**_impact_map(evidence_items=[item]))


def test_config_kind_requires_config_binding() -> None:
    item = _evidence_item_config_key()
    del item["config_binding"]
    with pytest.raises(ValidationError, match="CONFIG_KEY.*requires config_binding"):
        ImpactMap(**_impact_map(evidence_items=[item]))


def test_relations_ref_must_exist_in_evidence_items() -> None:
    with pytest.raises(ValidationError, match="ev-bogus"):
        ImpactMap(**_impact_map(
            evidence_items=[_evidence_item_method()],
            relations=[{"from_": "ev-001", "to": "ev-bogus", "kind": "calls"}],
        ))


def test_relations_valid_ref_passes() -> None:
    m = ImpactMap(**_impact_map(
        evidence_items=[_evidence_item_method(),
                        _evidence_item_method(id="ev-002", target="X#y")],
        relations=[{"from_": "ev-001", "to": "ev-002", "kind": "calls"}],
    ))
    assert len(m.relations) == 1


def test_dimension_status_rejects_unknown_key() -> None:
    bad = _impact_map()
    bad["dimension_status"]["sqlxx"] = "resolved"
    with pytest.raises(ValidationError):
        ImpactMap(**bad)


def test_v2_placeholder_kind_emits_no_error_but_warns() -> None:
    # MENU 是 v2 占位,v1 schema 接受但应该 warning(测 warning 由 stderr / log 捕获,
    # 这里仅校验解析不 raise)
    item = _evidence_item_method(kind="MENU", target="Home>Charging>Bulk Start",
                                 change_type="menu_flow_change")
    m = ImpactMap(**_impact_map(evidence_items=[item]))
    assert m.evidence_items[0].kind == "MENU"


def test_json_round_trip_preserves_three_dimension_payload() -> None:
    m = ImpactMap(**_impact_map(
        evidence_items=[
            _evidence_item_method(),
            _evidence_item_sql_table(),
            _evidence_item_config_key(),
        ],
        relations=[{"from_": "ev-001", "to": "ev-sql-001", "kind": "writes"}],
    ))
    js = m.model_dump_json()
    parsed = json.loads(js)
    m2 = ImpactMap.model_validate(parsed)
    assert m2.model_dump() == m.model_dump()
    # round-trip 必须经过 relations(否则漏掉 from_ alias 序列化 bug)
    assert len(m2.relations) == 1
    assert m2.relations[0].from_ == "ev-001"


def test_relation_serializes_to_wire_key_from_not_from_underscore() -> None:
    # 契约(01 design.md §relations):JSON key 是 "from" 不是 "from_"。
    # 默认 model_dump_json() 必须直接产出 wire key(下游消费者不应被迫传 by_alias=True)。
    m = ImpactMap(**_impact_map(
        evidence_items=[_evidence_item_method(),
                        _evidence_item_method(id="ev-002", target="X#y")],
        relations=[{"from_": "ev-001", "to": "ev-002", "kind": "calls"}],
    ))
    parsed = json.loads(m.model_dump_json())
    rel = parsed["relations"][0]
    assert "from" in rel and "from_" not in rel
    assert rel["from"] == "ev-001"


def test_evidence_item_id_must_be_unique_across_items() -> None:
    bad = _impact_map(evidence_items=[
        _evidence_item_method(),
        _evidence_item_method(target="other"),  # same id ev-001
    ])
    with pytest.raises(ValidationError, match="duplicate evidence_item id"):
        ImpactMap(**bad)


def test_matched_business_capabilities_confidence_bounded() -> None:
    bad = _impact_map(matched_business_capabilities=[
        {"capability": "billing-charging", "confidence": 1.5}
    ])
    with pytest.raises(ValidationError):
        ImpactMap(**bad)


def test_relation_kind_unknown_rejected() -> None:
    bad = _impact_map(
        evidence_items=[_evidence_item_method(),
                        _evidence_item_method(id="ev-002", target="X#y")],
        relations=[{"from_": "ev-001", "to": "ev-002", "kind": "bogus"}],
    )
    with pytest.raises(ValidationError):
        ImpactMap(**bad)


def test_impact_map_dimension_quality_default_empty_and_roundtrip():
    from contextos.impact_map.schema import ImpactMap

    # 默认空 dict(向后兼容: 旧数据无此字段也合法)
    m = ImpactMap(requirement_id="r1", requirement_summary="s")
    assert m.dimension_quality == {}

    # 可填 + re-parse(envelope.impact_map 要能被 ImpactMap.model_validate 回读)
    m2 = ImpactMap(requirement_id="r1", requirement_summary="s",
                   dimension_quality={"config": "fallback_only", "method": "strong"})
    dumped = m2.model_dump(mode="json")
    assert dumped["dimension_quality"] == {"config": "fallback_only", "method": "strong"}
    assert ImpactMap.model_validate(dumped).dimension_quality["config"] == "fallback_only"
