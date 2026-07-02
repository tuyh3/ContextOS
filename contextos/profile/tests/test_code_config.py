"""CodeConfig 新模式列表(search_source census 用)默认空 + 可加载。中性合成值。"""
from __future__ import annotations

from contextos.profile.schema import CodeConfig


def test_pattern_lists_default_empty():
    c = CodeConfig()
    assert c.dispatch_patterns == []
    assert c.carrier_read_patterns == []


def test_pattern_lists_load_neutral_values():
    c = CodeConfig(
        dispatch_patterns=["FrameworkDispatcher.callByName"],
        carrier_read_patterns=["StaticDict.getList", "ParamReader.getDetail"],
    )
    assert c.dispatch_patterns == ["FrameworkDispatcher.callByName"]
    assert c.carrier_read_patterns == ["StaticDict.getList", "ParamReader.getDetail"]


def test_extra_field_forbidden():
    # _StrictBase: extra=forbid 仍生效(没把 schema 改松)
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CodeConfig(unknown_field=1)
