"""ContextOS 需求拆解模块(02)。

把需求文本(text / docx)拆解为结构化 RequirementBreakdown,作为 04/05/06
证据桥的进料口。pipeline: adapter -> LLM 抽取 -> 能力分类 -> query 翻译。
"""
from contextos.requirement.adapters import AdapterResult, get_adapter
from contextos.requirement.classifier import classify
from contextos.requirement.extract import ExtractionResult, extract
from contextos.requirement.pipeline import breakdown
from contextos.requirement.schema import (
    CandidateConfigKey,
    CandidateName,
    CandidateTableTerm,
    DictHits,
    MatchedCapability,
    Queries,
    RequirementBreakdown,
)
from contextos.requirement.scope import ScopeVerdict, scope_judge
from contextos.requirement.translate import STATIC_GLOSSARY, detect_language, translate

__all__ = [
    # pipeline
    "breakdown",
    # schema
    "RequirementBreakdown",
    "MatchedCapability",
    "CandidateName",
    "CandidateTableTerm",
    "CandidateConfigKey",
    "DictHits",
    "Queries",
    # 步骤(供 smoke / 高级调用)
    "extract",
    "ExtractionResult",
    "classify",
    "translate",
    "detect_language",
    "STATIC_GLOSSARY",
    "scope_judge",
    "ScopeVerdict",
    # adapters
    "get_adapter",
    "AdapterResult",
]
