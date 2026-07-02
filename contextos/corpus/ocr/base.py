"""OcrBackend 抽象(桥内组件可插拔层次2)。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class OcrBackend(ABC):
    @abstractmethod
    def ocr(self, image_bytes: bytes) -> str:
        """识别一张图的文字; 失败应返回空串而非抛(由调用方决定降级)。"""
        ...
