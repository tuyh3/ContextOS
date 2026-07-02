"""Reranker 工厂(可插拔)。"""
from __future__ import annotations

from contextos.recall.reranker.base import Reranker


def make_reranker(cfg: object) -> Reranker:
    backend = getattr(cfg, "reranker_backend", "fake")
    if backend == "fake":
        from contextos.recall.reranker.fake import FakeReranker

        return FakeReranker()
    if backend == "bge":
        from contextos.recall.reranker.bge import BGEReranker

        return BGEReranker()
    raise ValueError(f"unknown reranker backend: {backend!r}")
