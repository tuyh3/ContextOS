"""SqlLineage + ConfigBinding 三维扩展模型(v1 01 §3.0.1 + §3.0.2)。

SQL 维扩展:借鉴 LP `lineage_out/lineage_edges.csv` schema + `sql_templates.jsonl`。
配置维扩展:借鉴 LP Phase 3 `config_bindings/entities/snapshots`。
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field

from contextos.impact_map.enums import (
    BindDirection,
    BindStrategy,
    BindType,
    EntityType,
    LineageType,
    RecoveryMode,
    RelationType,
    SnapshotEnv,
    SnapshotStrategy,
    SourceType,
    TableSizeTier,
)
from contextos.impact_map.evidence import _StrictBase


class TableRef(_StrictBase):
    """db.owner.table[.col] 四段式表 / 列引用(canonical key)。"""

    db: str
    owner: str
    table: str
    col: str | None = None


class SqlLineage(_StrictBase):
    """SQL 表 / 列 lineage 扩展字段。

    当 EvidenceItem.kind ∈ {SQL_TABLE, SQL_COLUMN, SQL_TEMPLATE} 时由 schema.py
    挂到 EvidenceItem.sql_lineage 字段(Task 4 顶层校验)。
    """

    relation_type: RelationType
    lineage_type: LineageType
    src: TableRef | None = None
    dst: TableRef
    evidence_count: Annotated[int, Field(gt=0)]
    sql_template_id: str | None = None
    recovery_mode: RecoveryMode
    branch_detected: bool = False
    unresolved_reason: str | None = None


class ConfigBinding(_StrictBase):
    """配置维 binding 扩展字段(LP Phase 3 schema 整合)。

    file/yaml/xml/properties 来源 + 4 取值 source_type + 6 取值 bind_type
    + LP D2 修复的 C+B 双策略 bind_strategy。
    DB CONFIG_TABLE 大表(>50K 行)走 structured_summary 分级快照(LP Phase 3 §4.1)。
    """

    entity_type: EntityType
    source_type: SourceType
    source_file: str | None = None
    source_framework: str | None = None
    bind_type: BindType
    bind_direction: BindDirection
    bind_strategy: BindStrategy

    # 当前快照
    value_raw: str | None = None
    value_type: str | None = None
    is_sensitive: bool = False
    snapshot_at: str | None = None       # ISO 8601 字符串
    snapshot_env: SnapshotEnv | None = None

    # CONFIG_TABLE 大表分级快照(仅 entity_type=db_table 时填)
    table_size_tier: TableSizeTier | None = None
    snapshot_strategy: SnapshotStrategy | None = None
    key_columns: list[str] | None = None
    value_columns: list[str] | None = None
    enum_counts: dict[str, dict[str, int]] | None = None
    total_rows: int | None = None
