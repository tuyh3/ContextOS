# contextos/orchestrator/rag_projection.py
"""RAG 投影特例(G5 / design §3.1 RAG 说明):RAG 吐 BUSINESS_DOC 文档候选(target=文档路径),
不走 candidate.target 跨桥对齐。改为扫所有 RAG 候选 snippet,建「实体名 -> max rerank_score」
字面命中查询器,供 corroboration 给 SQL/config 候选取 rag_score_bridge。
"""
from __future__ import annotations

import re

from contextos.orchestrator.provider_io import ProviderCandidate, _safe_float


class RagProjection:
    """字面命中查询器:candidate 实体名(表名/键名)是否在某 RAG 文档 snippet 里字面出现。

    命中:大小写不敏感 + 词边界(前后非 [a-z0-9_],防子串误配,如 PM 误配 PM_OFFER)。
    取所有命中文档 rerank_score 的 max。无命中 -> 0.0。
    """

    def __init__(self, docs: list[tuple[str, float]]) -> None:
        # docs = [(snippet, rerank_score), ...];内部转小写存
        self._docs = [(s.lower(), _safe_float(r)) for s, r in docs]

    @classmethod
    def from_candidates(cls, candidates: list[ProviderCandidate]) -> "RagProjection":
        # rerank_score 走 _safe_float:坏类型(非 coercible str 等)-> 0.0 不崩(fail-safe §5.1;
        # 真路径上 RagSignals.rerank_score 已是 Annotated[float],此为融合层 defense-in-depth)。
        docs = [(c.signals.get("snippet", "") or "",
                 _safe_float(c.signals.get("rerank_score", 0.0)))
                for c in candidates]
        return cls([(s, r) for s, r in docs if s])

    def score_for(self, entity_name: str) -> float:
        name = (entity_name or "").strip().lower()
        if not name:
            return 0.0
        pat = re.compile(r"(?<![a-z0-9_])" + re.escape(name) + r"(?![a-z0-9_])")
        best = 0.0
        for snippet, score in self._docs:
            if pat.search(snippet):
                best = max(best, score)
        return round(best, 4)


def build_rag_projection(rag_candidates: list[ProviderCandidate]) -> RagProjection:
    """从 RAG provider 候选(kind=BUSINESS_DOC)建投影查询器。"""
    return RagProjection.from_candidates(rag_candidates)
