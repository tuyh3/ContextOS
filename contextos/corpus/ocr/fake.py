"""FakeOcr: 确定性 OCR, 测试用, 零重依赖。"""
from __future__ import annotations

import hashlib

from contextos.corpus.ocr.base import OcrBackend


class FakeOcr(OcrBackend):
    def __init__(
        self, default_text: str = "FAKE_OCR_TEXT", by_hash: dict[str, str] | None = None
    ) -> None:
        self._default = default_text
        self._by_hash = by_hash or {}

    def ocr(self, image_bytes: bytes) -> str:
        h = hashlib.sha256(image_bytes).hexdigest()[:8]
        return self._by_hash.get(h, self._default)
