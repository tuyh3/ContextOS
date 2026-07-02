"""FakeLLM:脚本化响应 + 调用记录 + 与 structured() 协作。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from contextos.llm.fake import FakeLLM


class _Demo(BaseModel):
    name: str
    count: int


def test_fake_returns_queued_responses_in_order() -> None:
    llm = FakeLLM(responses=["first", "second"])
    assert llm.complete("a") == "first"
    assert llm.complete("b") == "second"


def test_fake_records_calls() -> None:
    llm = FakeLLM(responses=["x"])
    llm.complete("the prompt", system="sys")
    assert len(llm.calls) == 1
    assert llm.calls[0].prompt == "the prompt"
    assert llm.calls[0].system == "sys"


def test_fake_structured_uses_queued_json() -> None:
    llm = FakeLLM(responses=['{"name": "z", "count": 9}'])
    out = llm.structured("give demo", _Demo)
    assert isinstance(out, _Demo)
    assert out.name == "z" and out.count == 9


def test_fake_callable_handler_sees_prompt() -> None:
    def handler(prompt: str, system: str | None) -> str:
        return "ECHO:" + prompt

    llm = FakeLLM(handler=handler)
    assert llm.complete("hi") == "ECHO:hi"


def test_fake_raises_when_queue_exhausted() -> None:
    llm = FakeLLM(responses=["only one"])
    llm.complete("a")
    with pytest.raises(AssertionError):
        llm.complete("b")  # 队列空 -> 测试期望显式失败而非静默


def test_fake_requires_responses_or_handler() -> None:
    with pytest.raises(ValueError):
        FakeLLM()  # 两者都没给
