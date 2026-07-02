"""BGEReranker: cross-encoder 真实打分(BAAI/bge-reranker-v2-m3)。

复用已有依赖 sentence-transformers 的 CrossEncoder。本地推理。首次加载下权重。
"""
from __future__ import annotations

from contextos.recall.reranker.base import Reranker

_DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


class BGEReranker(Reranker):
    def __init__(self, model: str = _DEFAULT_MODEL, device: str = "cpu") -> None:
        from sentence_transformers import CrossEncoder

        self._ce = CrossEncoder(model, device=device, trust_remote_code=True)

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        pairs = [(query, p) for p in passages]
        scores = self._ce.predict(pairs)
        return [float(s) for s in scores]
