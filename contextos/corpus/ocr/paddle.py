"""PaddleOcr: 真实 OCR backend(optional dep `ocr`, 懒加载)。

中文+表格识别强。本地离线, 不传云(合规)。失败返回空串(由调用方决定降级)。
"""
from __future__ import annotations

import logging

from contextos.corpus.ocr.base import OcrBackend

_log = logging.getLogger(__name__)


class PaddleOcr(OcrBackend):
    def __init__(self, languages: list[str] | None = None) -> None:
        from paddleocr import PaddleOCR  # type: ignore[import-untyped]  # optional dep, 懒加载

        langs = languages or ["ch", "en"]
        # 每种语言一个引擎; PaddleOCR 的 ch 模型已含中英混排, en 兜英文密集场景
        self._engines = [
            PaddleOCR(use_angle_cls=True, lang=lang, show_log=False) for lang in langs
        ]

    def ocr(self, image_bytes: bytes) -> str:
        try:
            import cv2  # type: ignore[import-untyped]  # optional dep
            import numpy as np  # type: ignore[import-untyped]  # optional dep

            arr = cv2.imdecode(
                np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR
            )
            if arr is None:
                return ""
            lines: list[str] = []
            for engine in self._engines:
                result = engine.ocr(arr, cls=True)
                for page in result or []:
                    for entry in page or []:
                        # entry = [box, (text, score)]
                        if entry and len(entry) >= 2 and entry[1]:
                            lines.append(str(entry[1][0]))
                if lines:  # 第一个引擎出了结果就够(避免重复)
                    break
            return "\n".join(lines)
        except Exception as exc:  # OCR 失败不抛, 返回空串(fail-safe)
            _log.warning("paddle ocr failed: %s", exc)
            return ""
