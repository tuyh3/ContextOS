"""ContextOS Impact Map 输出格式契约 — v1 01 design.md SSOT。

公开 API:
- ImpactMap        — 顶层契约
- EvidenceItemWithDimensions / EvidenceItem  — 单条 evidence(三维通用 + 可选扩展)
- EvidenceRef      — 一条 evidence 来源 + 理由
- SqlLineage / TableRef / ConfigBinding  — 三维扩展模型
- MatchedCapability / EntrypointRef / ModuleRef / Relation  — 顶层嵌套模型
- 各 enum / Literal 类型(从 enums 模块 re-export)
"""
from contextos.impact_map.dimensions import ConfigBinding, SqlLineage, TableRef
from contextos.impact_map.enums import (
    KIND_CONFIG_DIMENSION,
    KIND_SQL_DIMENSION,
    KIND_V1_REACHABLE,
    KIND_V2_PLACEHOLDER,
    KNOWN_EVIDENCE_SOURCES,
    KNOWN_LIMITATION_CODES,
    BindDirection,
    BindStrategy,
    BindType,
    ChangeType,
    ConfidenceTier,
    DimensionKey,
    DimensionStatus,
    EntityType,
    EntrypointKind,
    Kind,
    LineageType,
    RecoveryMode,
    RelationKind,
    RelationType,
    SnapshotEnv,
    SnapshotStrategy,
    SourceType,
    TableSizeTier,
)
from contextos.impact_map.evidence import EvidenceItem, EvidenceRef
from contextos.impact_map.schema import (
    EntrypointRef,
    EvidenceItemWithDimensions,
    ImpactMap,
    MatchedCapability,
    ModuleRef,
    Relation,
)

__all__ = [
    # 顶层
    "ImpactMap",
    "EvidenceItemWithDimensions",
    "EvidenceItem",
    "EvidenceRef",
    "SqlLineage",
    "TableRef",
    "ConfigBinding",
    "MatchedCapability",
    "EntrypointRef",
    "ModuleRef",
    "Relation",
    # enum / Literal 类型
    "Kind",
    "ChangeType",
    "ConfidenceTier",
    "RelationType",
    "LineageType",
    "RecoveryMode",
    "EntityType",
    "SourceType",
    "BindType",
    "BindDirection",
    "BindStrategy",
    "DimensionStatus",
    "DimensionKey",
    "EntrypointKind",
    "SnapshotStrategy",
    "SnapshotEnv",
    "TableSizeTier",
    "RelationKind",
    # 开放枚举提示集
    "KNOWN_EVIDENCE_SOURCES",
    "KNOWN_LIMITATION_CODES",
    "KIND_V1_REACHABLE",
    "KIND_V2_PLACEHOLDER",
    "KIND_SQL_DIMENSION",
    "KIND_CONFIG_DIMENSION",
]
