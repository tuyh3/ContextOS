"""OCR backend 工厂(可插拔)。"""
from __future__ import annotations

from contextos.corpus.ocr.base import OcrBackend


def make_ocr(cfg: object) -> OcrBackend:
    backend = getattr(cfg, "backend", "fake")
    languages = list(getattr(cfg, "languages", ["ch", "en"]))
    if backend == "fake":
        from contextos.corpus.ocr.fake import FakeOcr

        return FakeOcr()
    if backend == "paddle":
        from contextos.corpus.ocr.paddle import PaddleOcr

        return PaddleOcr(languages=languages)
    if backend == "tesseract":  # 占位, 未实装 -> 明确报错(YAGNI, 需要时再加)
        raise ValueError("tesseract backend 未实装(本 plan 只装 fake + paddle)")
    raise ValueError(f"unknown ocr backend: {backend!r}")
