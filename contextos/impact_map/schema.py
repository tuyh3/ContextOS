"""顶层 Impact Map JSON 契约(v1 01 §2)。

构成:
- 顶层 ImpactMap(requirement_id / summary / 派生汇总 / dimension_status /
  known_limitations / evidence_items / relations / open_questions / metadata)
- MatchedCapability / EntrypointRef / ModuleRef / Relation 嵌套模型
- EvidenceItemWithDimensions:EvidenceItem 加 sql_lineage / config_binding 可选字段
- 跨字段 validator(kind <-> dimension extension 一致性 + relations id ref +
  evidence_items id 唯一性 + dimension_status 已知 key)

POC `EvidenceItem` dataclass + 字典枚举(CHANGE_TYPES/KINDS/CONFIDENCES/
EVIDENCE_SOURCES)完全替换。向后兼容 re-export 在 __init__.py(Task 5)。
"""
from __future__ import annotations

import warnings
from typing import Annotated, Any

from pydantic import Field, model_validator

from contextos.impact_map.dimensions import ConfigBinding, SqlLineage
from contextos.impact_map.enums import (
    KIND_CONFIG_DIMENSION,
    KIND_SQL_DIMENSION,
    KIND_V2_PLACEHOLDER,
    DimensionKey,
    DimensionQuality,
    DimensionStatus,
    EntrypointKind,
    RelationKind,
)
from contextos.impact_map.evidence import EvidenceItem, _StrictBase


class EvidenceItemWithDimensions(EvidenceItem):
    """EvidenceItem 加可选 sql_lineage / config_binding 字段。

    跨字段一致性(kind <-> extension)由 ImpactMap.validate_evidence_dimensions
    在顶层校验。本类只承载字段,不做单条 evidence 内的跨字段校验
    (避免 model_validator 重复执行 + 错误信息散乱)。
    """

    sql_lineage: SqlLineage | None = None
    config_binding: ConfigBinding | None = None


class MatchedCapability(_StrictBase):
    capability: str
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]


class EntrypointRef(_StrictBase):
    kind: EntrypointKind
    target: str


class ModuleRef(_StrictBase):
    sub_project: str
    business_domain: str | None = None


class Relation(_StrictBase):
    """relations[] 一条边 — from_ 用下划线后缀避免 Python 关键字 from。

    serialize_by_alias=True 使默认 model_dump_json() 输出 wire key "from"(契约
    见 01 design.md §relations),而非 Python 字段名 "from_";populate_by_name=True
    使 Python 侧 Relation(from_=...) 构造与 {"from": ...} 反序列化两条路都通。
    """

    from_: str = Field(..., alias="from")
    to: str
    kind: RelationKind

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
        "serialize_by_alias": True,
    }


class ImpactMap(_StrictBase):
    """顶层契约。被 08 数据流编排填充,被 5 能力 + 09 评测消费。"""

    requirement_id: str
    requirement_summary: str
    version: str = "1.0"

    matched_business_capabilities: list[MatchedCapability] = Field(default_factory=list)
    candidate_entrypoints: list[EntrypointRef] = Field(default_factory=list)
    modules_touched: list[ModuleRef] = Field(default_factory=list)

    dimension_status: dict[DimensionKey, DimensionStatus] = Field(default_factory=dict)
    # 质量轴(spec 2026-06-17 §5): 与 dimension_status(覆盖轴)正交。additive 默认空,
    # 旧序列化数据无此字段反序列化仍合法。assemble._dimension_quality 填充。
    dimension_quality: dict[DimensionKey, DimensionQuality] = Field(default_factory=dict)

    known_limitations: list[str] = Field(default_factory=list)
    evidence_items: list[EvidenceItemWithDimensions] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_evidence_id_unique(self) -> "ImpactMap":
        seen = set()
        for it in self.evidence_items:
            if it.id in seen:
                raise ValueError(f"duplicate evidence_item id: {it.id!r}")
            seen.add(it.id)
        return self

    @model_validator(mode="after")
    def _check_kind_dimension_consistency(self) -> "ImpactMap":
        for it in self.evidence_items:
            if it.kind in KIND_SQL_DIMENSION:
                if it.sql_lineage is None:
                    raise ValueError(
                        f"kind={it.kind} (id={it.id!r}) requires sql_lineage"
                    )
                if it.config_binding is not None:
                    raise ValueError(
                        f"kind={it.kind} (id={it.id!r}) "
                        "should not have config_binding"
                    )
            elif it.kind in KIND_CONFIG_DIMENSION:
                if it.config_binding is None:
                    raise ValueError(
                        f"kind={it.kind} (id={it.id!r}) requires config_binding"
                    )
                if it.sql_lineage is not None:
                    raise ValueError(
                        f"kind={it.kind} (id={it.id!r}) "
                        "should not have sql_lineage"
                    )
            else:
                if it.sql_lineage is not None:
                    raise ValueError(
                        f"kind={it.kind} (id={it.id!r}) "
                        "should not have sql_lineage"
                    )
                if it.config_binding is not None:
                    raise ValueError(
                        f"kind={it.kind} (id={it.id!r}) "
                        "should not have config_binding"
                    )

            if it.kind in KIND_V2_PLACEHOLDER:
                warnings.warn(
                    f"kind={it.kind!r} (id={it.id!r}) is a v2 placeholder; "
                    "no v1 provider should emit it",
                    UserWarning,
                    stacklevel=2,
                )
        return self

    @model_validator(mode="after")
    def _check_relations_ref_valid(self) -> "ImpactMap":
        if not self.relations:
            return self
        valid_ids = {it.id for it in self.evidence_items}
        for r in self.relations:
            if r.from_ not in valid_ids:
                raise ValueError(
                    f"relations[].from references missing id: {r.from_!r}"
                )
            if r.to not in valid_ids:
                raise ValueError(
                    f"relations[].to references missing id: {r.to!r}"
                )
        return self
