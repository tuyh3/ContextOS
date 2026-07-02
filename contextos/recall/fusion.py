"""Reciprocal Rank Fusion. dense 开关打开时融合 sparse + dense 两路排名。

RRF_score(d) = sum_path 1 / (k + rank_path(d))。k=60 业界默认(Cohere/Vespa)。
分数绝对值不可比但排名可比 -> 跨路稳定融合。
"""
from __future__ import annotations


def rrf_merge(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, start=1):
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
