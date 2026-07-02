"""enums.py SSOT 测试:Literal 取值数量 + 已知集合内容 + v1/v2 划分。"""
from __future__ import annotations

import typing
from typing import get_args

from contextos.impact_map.enums import (
    KIND_CONFIG_DIMENSION,
    KIND_SQL_DIMENSION,
    KIND_V1_REACHABLE,
    KIND_V2_PLACEHOLDER,
    KNOWN_EVIDENCE_SOURCES,
    KNOWN_LIMITATION_CODES,
    DimensionQuality,
    Kind,
)


def test_kind_total_count_and_frozensets_cover_literal() -> None:
    # Kind Literal = 19 取值(16 v1 可达 + 3 v2 占位);改 design.md §3.1 时同步改这里。
    kind_values = set(typing.get_args(Kind))
    assert len(kind_values) == 19
    assert len(KIND_V1_REACHABLE) == 16
    assert len(KIND_V2_PLACEHOLDER) == 3
    # 两个 frozenset 并起来必须正好覆盖 Kind Literal 全集(防漏挂 / 错挂)
    assert KIND_V1_REACHABLE | KIND_V2_PLACEHOLDER == kind_values


def test_kind_v1_v2_disjoint_and_cover_all_3_dimensions() -> None:
    assert KIND_V1_REACHABLE.isdisjoint(KIND_V2_PLACEHOLDER)
    # SQL / 配置维全在 v1 可达集合内
    assert KIND_SQL_DIMENSION <= KIND_V1_REACHABLE
    assert KIND_CONFIG_DIMENSION <= KIND_V1_REACHABLE
    # SQL / 配置维互不重叠
    assert KIND_SQL_DIMENSION.isdisjoint(KIND_CONFIG_DIMENSION)


def test_known_evidence_sources_contains_v1_5_bridges() -> None:
    # 桥1 / 2 / 3 / 5 的标志性来源必在(桥4 历史空号不补)
    assert "jdt-ls-workspaceSymbol" in KNOWN_EVIDENCE_SOURCES
    assert "rag-cross-encoder" in KNOWN_EVIDENCE_SOURCES
    assert "dict-interface" in KNOWN_EVIDENCE_SOURCES
    assert "llm-rerank" in KNOWN_EVIDENCE_SOURCES
    # LP 整合的 SQL / 配置维桥
    assert "lp-sql-recover-literal" in KNOWN_EVIDENCE_SOURCES
    assert "lp-bind-resolver-prefix" in KNOWN_EVIDENCE_SOURCES


def test_known_limitation_codes_contains_lp_d10_and_lua() -> None:
    assert "dataflow_write_side_table_missing" in KNOWN_LIMITATION_CODES
    assert "ocs_lua_script_field" in KNOWN_LIMITATION_CODES


def test_kind_sql_dimension_exact_three_values() -> None:
    assert KIND_SQL_DIMENSION == {"SQL_TABLE", "SQL_COLUMN", "SQL_TEMPLATE"}


def test_kind_config_dimension_exact_four_values() -> None:
    assert KIND_CONFIG_DIMENSION == {"CONFIG_FILE", "CONFIG_KEY", "CONFIG_TABLE", "RULE_SET"}


def test_object_dependency_blind_spots_registered():
    from contextos.impact_map.enums import KNOWN_LIMITATION_CODES
    assert "object_dependency_blind_spots" in KNOWN_LIMITATION_CODES


def test_dimension_quality_has_four_values():
    assert set(get_args(DimensionQuality)) == {
        "strong", "low_confidence", "fallback_only", "not_applicable"
    }
