"""FakeLLM -- 确定化测试替身。

两种用法:
- FakeLLM(responses=[...]):按队列依序返回(structured 场景放 JSON 串);
- FakeLLM(handler=fn):fn(prompt, system) -> str,动态生成。
记录每次 complete() 调用到 .calls,便于断言。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from contextos.llm.base import LLMProvider


@dataclass
class FakeCall:
    prompt: str
    system: str | None
    temperature: float | None
    max_tokens: int | None


class FakeLLM(LLMProvider):
    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        handler: Callable[[str, str | None], str] | None = None,
    ) -> None:
        if responses is None and handler is None:
            raise ValueError("FakeLLM needs either responses=[...] or handler=fn")
        self._responses = list(responses) if responses is not None else None
        self._handler = handler
        self.calls: list[FakeCall] = []

    def complete(self, prompt, *, system=None, temperature=None, max_tokens=None) -> str:
        self.calls.append(FakeCall(prompt, system, temperature, max_tokens))
        if self._handler is not None:
            return self._handler(prompt, system)
        assert self._responses, "FakeLLM response queue exhausted"
        return self._responses.pop(0)
