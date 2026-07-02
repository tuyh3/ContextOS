"""03 桥 2 检索的 provider 专属信号契约。

RagSignals = rag 候选的 provider 专属信号(rerank_score / snippet / evidence_origin /
lineno), 校验后 model_dump() 进 ProviderCandidate.signals(信封保持 provider-agnostic,
对齐 08 §2 + 04 CodeSearchSignals 同款做法)。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class _StrictBase(BaseModel):
    model_config = {"extra": "forbid"}


class RagSignals(_StrictBase):
    rerank_score: Annotated[float, Field(ge=0.0)]      # reranker 原始分(Fake 0-1; BGE sigmoid 0-1)
    snippet: str                                        # 命中窗口文本(已截断)
    evidence_origin: Literal["text", "ocr"] = "text"    # 命中点是正文还是截图 OCR 物化文本
    lineno: int = -1                                    # best passage 的命中行(1-based)
    num_hits: Annotated[int, Field(ge=0)] = 0           # 该 doc 命中行数
