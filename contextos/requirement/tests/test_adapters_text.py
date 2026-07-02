from __future__ import annotations

from contextos.requirement.adapters import AdapterResult, get_adapter


def test_text_passthrough_strips_and_keeps_content():
    adapter = get_adapter("text")
    res = adapter("  新增动态计费批量操作  \n\n\n详见附件  ")
    assert isinstance(res, AdapterResult)
    assert "新增动态计费批量操作" in res.raw_text
    assert res.raw_text == res.raw_text.strip()
    assert res.open_questions == []


def test_text_empty_yields_open_question():
    adapter = get_adapter("text")
    res = adapter("   \n  ")
    assert res.raw_text == ""
    assert res.open_questions and "解析失败" in res.open_questions[0]


def test_get_adapter_unknown_source_kind_raises():
    import pytest

    with pytest.raises(ValueError, match="unsupported source_kind"):
        get_adapter("carrier-pigeon")


def test_get_adapter_respects_profile_disable():
    import pytest

    from contextos.profile.schema import InputConfig

    class _P:
        input = InputConfig(adapters={"text": False, "docx": True})

    with pytest.raises(ValueError, match="disabled"):
        get_adapter("text", profile=_P())
