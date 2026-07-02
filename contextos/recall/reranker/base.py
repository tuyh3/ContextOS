"""Reranker 抽象(桥内组件可插拔)。cross-encoder 逐对 (query, passage) 打分。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Reranker(ABC):
    @abstractmethod
    def score(self, query: str, passages: list[str]) -> list[float]:
        """返回与 passages 等长的分数列表(越高越相关)。"""
        ...
