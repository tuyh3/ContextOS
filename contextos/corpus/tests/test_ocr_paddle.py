import base64

import pytest

_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.mark.integration
def test_paddle_ocr_smoke():
    pytest.importorskip("paddleocr")
    from contextos.corpus.ocr.paddle import PaddleOcr
    from contextos.corpus.ocr.base import OcrBackend
    o = PaddleOcr(languages=["en"])
    assert isinstance(o, OcrBackend)
    # 1x1 空图 -> 不抛, 返回 str(可能空串)
    result = o.ocr(_PNG_1x1)
    assert isinstance(result, str)
