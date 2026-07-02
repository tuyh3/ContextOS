"""FakeReranker: 确定性词重叠打分, 测试用, 零重依赖。"""
from __future__ import annotations

import re

from contextos.recall.reranker.base import Reranker

_TOKEN = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")


def _tokens(s: str) -> set[str]:
    return set(_TOKEN.findall(s.lower()))


class FakeReranker(Reranker):
    def score(self, query: str, passages: list[str]) -> list[float]:
        q = _tokens(query)
        if not q:
            return [0.0 for _ in passages]
        out: list[float] = []
        for p in passages:
            pt = _tokens(p)
            overlap = len(q & pt) / len(q)
            out.append(round(overlap, 4))
        return out
