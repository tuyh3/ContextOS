def test_fake_ocr_default_text():
    from contextos.corpus.ocr.fake import FakeOcr
    o = FakeOcr(default_text="HELLO")
    assert o.ocr(b"anybytes") == "HELLO"


def test_fake_ocr_by_hash_mapping():
    import hashlib
    from contextos.corpus.ocr.fake import FakeOcr
    img = b"img-content"
    h = hashlib.sha256(img).hexdigest()[:8]
    o = FakeOcr(default_text="DEFAULT", by_hash={h: "CONF_PROVINCE_TAX"})
    assert o.ocr(img) == "CONF_PROVINCE_TAX"
    assert o.ocr(b"other") == "DEFAULT"


def test_make_ocr_factory_returns_fake():
    from contextos.profile.schema import OcrConfig
    from contextos.corpus.ocr import make_ocr
    from contextos.corpus.ocr.base import OcrBackend
    o = make_ocr(OcrConfig(backend="fake"))
    assert isinstance(o, OcrBackend)
    assert o.ocr(b"x")  # 非空


def test_make_ocr_unknown_backend_raises():
    import pytest
    from contextos.corpus.ocr import make_ocr

    class _Cfg:
        backend = "bogus"
        languages = ["en"]

    with pytest.raises(ValueError):
        make_ocr(_Cfg())
