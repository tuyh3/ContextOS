"""Guard 2 grounding(spec 4.2):候选 source_span 对 raw_text 确定性核验, 0 token。

LLM 候选: normalize(source_span) 是否为 normalize(raw_text) 子串。命中=有出处(留);
空/不命中=脑补(砍, 防下游空跑)。正则基线候选(source=regex): 本就从原文确定性抽出,
by-construction 有出处, 豁免子串检查(黏合 CamelCase 可能不是原文字面子串)。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Protocol, TypeVar


class _GroundableCandidate(Protocol):
    """三类候选(CandidateName / CandidateTableTerm / CandidateConfigKey)的共有结构。"""
    source: str
    source_span: str
    term: str


# bound 到 Protocol: 保留入参具体类型(传 list[CandidateName] 回 list[CandidateName]),
# 同时让静态检查器知道候选有 source / source_span / term。
_Candidate = TypeVar("_Candidate", bound=_GroundableCandidate)

_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    """归一化: NFKC(折全/半角 + 兼容字符)+ 去所有空白 + casefold。"""
    return _WS_RE.sub("", unicodedata.normalize("NFKC", s)).casefold()


def is_grounded(candidate: _GroundableCandidate, raw_norm: str) -> bool:
    if getattr(candidate, "source", "") == "regex":
        return True
    span = (getattr(candidate, "source_span", "") or "").strip()
    if not span:
        return False
    return _normalize(span) in raw_norm


def ground_candidates(
    candidates: list[_Candidate], raw_text: str
) -> tuple[list[_Candidate], list[_Candidate]]:
    """返回 (留存, 砍掉)。正则基线 source_span 空时回填 term(留痕)。"""
    raw_norm = _normalize(raw_text)
    kept: list = []
    dropped: list = []
    for c in candidates:
        if is_grounded(c, raw_norm):
            if c.source == "regex" and not c.source_span:
                c.source_span = c.term
            kept.append(c)
        else:
            dropped.append(c)
    return kept, dropped


def coverage(kept_total: int, all_total: int) -> float:
    """有出处候选数 / 抽出总数。总数 0 -> 1.0(无候选即无脑补)。"""
    return 1.0 if all_total == 0 else kept_total / all_total
