"""POC-era Impact Map schema (dataclass) — frozen shim.

为什么单独一个文件(而不是放 v1 impact_map 包):
- POC 阶段的 gold 评测产物(`data/poc/samples/*-gold.json`)是 POC 口径:
  change_type=NEW/MODIFY/CONFIG/DB,confidence=HIGH/MEDIUM/LOW 字符串,
  evidence_source+rationale 单源。
- v1 的 `contextos.impact_map` 是 pydantic v2 三维契约(confidence=float +
  confidence_tier + evidence_refs 多源),与 POC 口径不兼容,会拒绝 POC gold.json。
- Plan 01 把 impact_map/schema.py 从 POC dataclass 重写成 v1 pydantic 后,原
  `from contextos.impact_map.schema import ImpactMap / EvidenceItem /
  validate_impact_map` 全部失效。
- 不把 POC 口径塞进 v1 契约包(保持契约纯净),也不写未测的口径迁移代码(本仓
  worktree 无 sample xlsx / gold.json,迁移无法验证)。故 POC schema 原样冻结在此。

消费者(全是 POC 阶段工具,操作 gitignored 的 data/poc/):
- contextos/recall/historical_fpa.py(to_evidence_items 产 POC gold)
- scripts/poc_t1_extract_gold.py / scripts/poc_t1_validate_gold.py

TODO(Plan 09 评测重建): gold 产线迁到 v1 三维 gold
(contextos.impact_map.EvidenceItemWithDimensions / ImpactMap),届时删本文件 +
更新上述消费者。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CHANGE_TYPES = {"NEW", "MODIFY", "CONFIG", "DB"}
KINDS = {"METHOD", "CLASS", "FIELD", "INTERFACE", "SQL_TABLE", "SQL_COLUMN", "CONFIG_KEY", "FILE", "OTHER"}
CONFIDENCES = {"HIGH", "MEDIUM", "LOW"}
EVIDENCE_SOURCES = {
    "human-annotation",         # 人工标(POC 不用,但保留供 v1 真人 review 流程)
    "historical-fpa",           # Step 6.10 自动从 FPA xlsx 抽出的 gold evidence
    "jdt-ls",                   # Task 3 JDT LS binding 结果
    "sql-resolver",             # SQL 字面值 -> 表/字段 resolver
    "config-resolver",          # 配置表 / config key resolver
    "embedding-recall",         # Task 7 召回结果
    "manual-merge",             # Step 10.3 Claude + user 组装
    "codex-independent-merge",  # Step 10.3.5 Codex (GPT 5.5x) 独立组装
}


@dataclass
class EvidenceItem:
    """POC gold-evidence 单条(POC 口径,见模块 docstring)。"""

    id: str
    change_type: str  # NEW / MODIFY / CONFIG / DB
    kind: str  # METHOD / CLASS / FIELD / INTERFACE / SQL_TABLE / SQL_COLUMN / CONFIG_KEY / FILE / OTHER
    target: str  # Java FQN or table name or config key
    file: str  # repo-relative path
    line_start: int
    line_end: int
    confidence: str  # HIGH / MEDIUM / LOW
    evidence_source: str
    rationale: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.change_type not in CHANGE_TYPES:
            raise ValueError(f"change_type {self.change_type!r} not in {CHANGE_TYPES}")
        if self.kind not in KINDS:
            raise ValueError(f"kind {self.kind!r} not in {KINDS}")
        if self.confidence not in CONFIDENCES:
            raise ValueError(f"confidence {self.confidence!r} not in {CONFIDENCES}")
        if self.evidence_source not in EVIDENCE_SOURCES:
            raise ValueError(f"evidence_source {self.evidence_source!r} not in {EVIDENCE_SOURCES}")


@dataclass
class ImpactMap:
    """POC Impact Map 容器(POC 口径,见模块 docstring)。"""

    requirement_id: str
    requirement_summary: str
    version: str
    evidence_items: list[EvidenceItem]
    metadata: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImpactMap:
        items = [EvidenceItem(**item) for item in data["evidence_items"]]
        for item in items:
            item.validate()
        return cls(
            requirement_id=data["requirement_id"],
            requirement_summary=data["requirement_summary"],
            version=data["version"],
            evidence_items=items,
            metadata=data["metadata"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "requirement_summary": self.requirement_summary,
            "version": self.version,
            "evidence_items": [
                {
                    "id": i.id,
                    "change_type": i.change_type,
                    "kind": i.kind,
                    "target": i.target,
                    "file": i.file,
                    "line_start": i.line_start,
                    "line_end": i.line_end,
                    "confidence": i.confidence,
                    "evidence_source": i.evidence_source,
                    "rationale": i.rationale,
                    "metadata": i.metadata,
                }
                for i in self.evidence_items
            ],
            "metadata": self.metadata,
        }


def validate_impact_map(m: ImpactMap) -> None:
    if not m.requirement_id:
        raise ValueError("requirement_id is required")
    if not m.evidence_items:
        raise ValueError("evidence_items cannot be empty")
    for item in m.evidence_items:
        item.validate()
