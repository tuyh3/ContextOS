"""04 代码搜索引擎的输入/信号契约。对齐 04 §7。

输入 CodeSearchQuery = 02 RequirementBreakdown 经 input_adapter 归一后的形态。
CodeSearchSignals = 04 候选的 provider 专属信号,dump 进 ProviderCandidate.signals。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class _StrictBase(BaseModel):
    model_config = {"extra": "forbid"}


# 04 §7 input search_terms[].kind(= 02 CodeNameKind:shouty/camelcase/proper_noun/other)
SearchTermKind = Literal["shouty", "camelcase", "proper_noun", "other"]
# 04 §7 signals.call_direction(本 plan 只产 seed;caller/callee 留 Plan 04b)
CallDirection = Literal["seed", "caller", "callee"]
# 04 §7 signals.binding_source
BindingSource = Literal["jdt-ls", "scip-java", "tree-sitter"]


class SearchTerm(_StrictBase):
    term: str
    kind: SearchTermKind


class CodeSearchQuery(_StrictBase):
    """02 -> 04 归一后的输入(04 §7 input)。"""

    search_terms: list[SearchTerm] = Field(default_factory=list)
    matched_capability: str = ""
    sub_project_hints: list[str] = Field(default_factory=list)


class CodeSearchSignals(_StrictBase):
    """04 候选的 provider 专属信号(04 §7 candidates[].signals)。

    file/line 也放进信号(envelope 的 ProviderCandidate 保持 provider-agnostic,
    只留 target/kind/signals;具体定位信息归 signals)。
    """

    name_match_strength: Annotated[float, Field(ge=0.0, le=1.0)]
    call_distance_from_seed: Annotated[int, Field(ge=0)]
    call_direction: CallDirection
    binding_source: BindingSource = "jdt-ls"
    file: str = ""
    line_start: int = -1
    line_end: int = -1
    # 04b freshness 透传(spec §9; U0 信封不动, 字段走 signals)
    projection_build_id: str = ""
    indexed_commit: str = ""
    projection_status: str = ""
