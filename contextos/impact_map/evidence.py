"""EvidenceRef + EvidenceItem 13 通用字段(三维共享)。

三维扩展字段(sql_lineage / config_binding)在 dimensions.py 里 attach 到
EvidenceItem(Task 3)。本文件只放通用底盘。
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from contextos.impact_map.enums import (
    ChangeType,
    ConfidenceTier,
    EntrypointKind,
    Kind,
)


class _StrictBase(BaseModel):
    """与 Plan 0 profile.schema._StrictBase 同义:extra=forbid。"""
    model_config = {"extra": "forbid"}


class EvidenceRef(_StrictBase):
    """一条 evidence 来源 + 理由,见 v1 01 §3.3。source 是开放枚举(KNOWN_EVIDENCE_SOURCES 提示集)。"""

    source: str
    rerank_score: Annotated[float, Field(ge=0.0, le=1.0)]
    content_raw: str | None = None      # audit 用,完整 1KB 内 quote
    content_summary: str | None = None   # LLM 上下文经济用


class EvidenceItem(_StrictBase):
    """三维共享的 13 通用字段 + 可选扩展(由 dimensions.py 在 Task 3 attach 三维字段)。"""

    id: str
    target: str
    kind: Kind
    file: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    sub_project: str | None = None
    business_domain: str | None = None
    entrypoint_kind: EntrypointKind | None = None
    change_type: ChangeType
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    confidence_tier: ConfidenceTier
    evidence_refs: list[EvidenceRef] = Field(..., min_length=1)
    reasoning: str | None = None
    miss_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
