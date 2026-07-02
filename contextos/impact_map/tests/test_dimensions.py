"""SqlLineage + ConfigBinding 三维扩展模型 + nested TableRef 校验。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contextos.impact_map.dimensions import (
    ConfigBinding,
    SqlLineage,
    TableRef,
)


def _table_ref(**overrides) -> dict:
    base = {"db": "CCRM3", "owner": "UPC", "table": "PM_OFFER_CHA", "col": "CHA_VALUE"}
    return {**base, **overrides}


def _sql_lineage(**overrides) -> dict:
    base = {
        "relation_type": "INSERT_SELECT",
        "lineage_type": "DIRECT",
        "src": {"db": "CCRM3", "owner": "UPC", "table": "PM_OFFER_BASE", "col": None},
        "dst": _table_ref(),
        "evidence_count": 3,
        "sql_template_id": "T4BD741F3CE",
        "recovery_mode": "string_builder",
        "branch_detected": False,
        "unresolved_reason": None,
    }
    return {**base, **overrides}


def _config_binding(**overrides) -> dict:
    base = {
        "entity_type": "file_key",
        "source_type": "file",
        "source_file": "config/application-prod.properties",
        "source_framework": "spring",
        "bind_type": "java_method",
        "bind_direction": "read",
        "bind_strategy": "annotation_prefix_match",
        "value_raw": "jdbc:oracle:thin:@host:1521:CCRM3",
        "value_type": "string",
        "is_sensitive": False,
        "snapshot_at": "2026-05-30T10:00:00Z",
        "snapshot_env": "prod",
    }
    return {**base, **overrides}


def test_sql_lineage_minimal_parses() -> None:
    lineage = SqlLineage(**_sql_lineage())
    assert lineage.relation_type == "INSERT_SELECT"
    assert lineage.dst.table == "PM_OFFER_CHA"
    assert lineage.src is not None and lineage.src.col is None


def test_sql_lineage_src_optional_for_pure_dst_evidence() -> None:
    lineage = SqlLineage(**_sql_lineage(src=None))
    assert lineage.src is None


def test_sql_lineage_evidence_count_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        SqlLineage(**_sql_lineage(evidence_count=0))


def test_sql_lineage_unknown_relation_type_rejected() -> None:
    with pytest.raises(ValidationError):
        SqlLineage(**_sql_lineage(relation_type="UPSERT"))  # not in 8 取值


def test_sql_lineage_unknown_recovery_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        SqlLineage(**_sql_lineage(recovery_mode="ast_magic"))  # not in 7 取值


def test_sql_lineage_unresolved_reason_replaces_resolution() -> None:
    # parse 失败时:relation_type 仍要填(用 SUBQUERY 之类占位),但 unresolved_reason 不为 None
    lineage = SqlLineage(
        **_sql_lineage(unresolved_reason="parse failed (AST + regex)", src=None)
    )
    assert lineage.unresolved_reason == "parse failed (AST + regex)"


def test_table_ref_col_can_be_none() -> None:
    ref = TableRef(db="CCRM3", owner="UPC", table="PM_OFFER_CHA", col=None)
    assert ref.col is None


def test_config_binding_minimal_parses() -> None:
    cfg = ConfigBinding(**_config_binding())
    assert cfg.entity_type == "file_key"
    assert cfg.bind_strategy == "annotation_prefix_match"


def test_config_binding_db_table_large_table_fields() -> None:
    cfg = ConfigBinding(**_config_binding(
        entity_type="db_table",
        source_type="db_table",
        source_file=None,
        bind_type="table",
        table_size_tier="large",
        snapshot_strategy="structured_summary",
        key_columns=["ACCESS_NUM", "WHITE_TYPE"],
        value_columns=["USE_FLAG", "VALID_DATE"],
        enum_counts={"WHITE_TYPE": {"MZA_FREE_PROM": 1200, "HALF_PRICE": 45000}},
        total_rows=83_000_000,
    ))
    assert cfg.table_size_tier == "large"
    assert cfg.total_rows == 83_000_000


def test_config_binding_sensitive_value_flag() -> None:
    cfg = ConfigBinding(**_config_binding(is_sensitive=True,
                                          value_raw="****d5f3"))
    assert cfg.is_sensitive is True


def test_config_binding_unknown_bind_strategy_rejected() -> None:
    with pytest.raises(ValidationError):
        ConfigBinding(**_config_binding(bind_strategy="brute_force"))


def test_config_binding_unknown_snapshot_env_rejected() -> None:
    with pytest.raises(ValidationError):
        ConfigBinding(**_config_binding(snapshot_env="staging"))


def test_config_binding_typo_field_rejected() -> None:
    with pytest.raises(ValidationError):
        ConfigBinding(**_config_binding(bind_directionn="read"))
