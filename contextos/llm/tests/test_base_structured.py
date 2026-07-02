"""LLMProvider.structured() 共享逻辑:JSON 提取 / 校验 / 重试 / 异常。

用一个最小 _ScriptedProvider(只实现 complete() 返回预设串)驱动 structured(),
不碰真实 HTTP,也不依赖 FakeLLM(FakeLLM 是 Task 3)。
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from contextos.llm.base import LLMProvider, LLMStructuredError


class _Demo(BaseModel):
    name: str
    count: int


class _ScriptedProvider(LLMProvider):
    """按队列返回预设 complete() 响应,记录收到的 prompt。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt, *, system=None, temperature=None, max_tokens=None) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0)


def test_structured_parses_clean_json() -> None:
    p = _ScriptedProvider(['{"name": "a", "count": 3}'])
    out = p.structured("give me a demo", _Demo)
    assert isinstance(out, _Demo)
    assert out.name == "a" and out.count == 3


def test_structured_strips_markdown_fences() -> None:
    p = _ScriptedProvider(['```json\n{"name": "b", "count": 1}\n```'])
    out = p.structured("x", _Demo)
    assert out.name == "b"


def test_structured_retries_on_invalid_then_succeeds() -> None:
    p = _ScriptedProvider(['not json at all', '{"name": "c", "count": 2}'])
    out = p.structured("x", _Demo, max_retries=2)
    assert out.count == 2
    # 第二次 prompt 应包含错误反馈(让模型自纠)
    assert len(p.prompts) == 2
    assert "invalid" in p.prompts[1].lower() or "json" in p.prompts[1].lower()


def test_structured_raises_after_exhausting_retries() -> None:
    p = _ScriptedProvider(['nope', 'still nope', 'nope again'])
    with pytest.raises(LLMStructuredError):
        p.structured("x", _Demo, max_retries=2)  # 1 + 2 = 3 次尝试全失败


def test_structured_rejects_schema_violation() -> None:
    # JSON 合法但缺字段 -> pydantic 校验失败 -> 触发重试 -> 仍失败 -> raise
    p = _ScriptedProvider(['{"name": "x"}', '{"name": "x"}'])
    with pytest.raises(LLMStructuredError):
        p.structured("x", _Demo, max_retries=1)


def test_complete_is_abstract() -> None:
    with pytest.raises(TypeError):
        LLMProvider()  # 抽象类不能实例化
