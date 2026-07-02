"""LLMProvider 抽象 + structured() 共享逻辑 + 异常类型。

设计:complete() 是唯一抽象方法(各 provider 实现真正的文本生成);
structured() 是基类共享具体方法,把"产 JSON -> 解析 -> pydantic 校验 -> 失败重试"
统一实现一次,所有 provider(含 FakeLLM)自动复用。
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class LLMError(Exception):
    """contextos.llm 顶层异常基类。"""


class LLMConfigError(LLMError):
    """profile.llm 配置不足以构建 provider。"""


class LLMStructuredError(LLMError):
    """structured() 在重试耗尽后仍无法得到合法对象。"""


class LLMHTTPError(LLMError):
    """真实 provider HTTP 调用失败。"""


def _strip_json_fences(text: str) -> str:
    """去掉 ```json ... ``` 围栏,返回中间内容。

    只处理"整段被围栏包裹"或"无围栏"两种;若 LLM 在闭合围栏后还跟了尾随文本
    (` ```json\\n{...}\\n```\\nSee above `),$ 锚点匹配不到闭合围栏,中间内容仍含
    ``` -> json.loads 失败 -> 由 structured() 的重试路径恢复。不在此函数兜底
    (别为这种少见情形把正则改复杂)。
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        # 去首尾围栏行
        stripped = _FENCE_RE.sub("", stripped).strip()
    return stripped


class LLMProvider(ABC):
    """LLM provider 抽象。子类只需实现 complete()。"""

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """产一段文本。各 provider 实现真正的生成。"""
        raise NotImplementedError

    def structured(
        self,
        prompt: str,
        schema: type[ModelT],
        *,
        system: str | None = None,
        max_retries: int = 2,
    ) -> ModelT:
        """让模型产 JSON 并用 schema 校验,失败带错误反馈重试 max_retries 次。"""
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        base_instruction = (
            f"{prompt}\n\n"
            "Respond with ONLY a single valid JSON object matching this JSON Schema. "
            "No markdown code fences, no commentary, JSON only:\n"
            f"{schema_json}"
        )
        instruction = base_instruction
        last_err: Exception | None = None
        for _ in range(max_retries + 1):
            raw = self.complete(instruction, system=system)
            text = _strip_json_fences(raw)
            try:
                data = json.loads(text)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = e
                instruction = (
                    f"{base_instruction}\n\n"
                    f"Your previous response was invalid ({type(e).__name__}: {e}). "
                    "Return corrected JSON only."
                )
        raise LLMStructuredError(
            f"structured() failed after {max_retries + 1} attempts: {last_err}"
        )
