"""ContextOS LLM provider 共享层。

公开 API:
- LLMProvider          -- 抽象(complete + structured)
- OpenAICompatProvider -- 真实 OpenAI 兼容 HTTP client
- FakeLLM              -- 确定化测试替身
- provider_from_profile -- 从 profile.llm 工厂化
- LLMError / LLMConfigError / LLMStructuredError / LLMHTTPError -- 异常
"""
from contextos.llm.base import (
    LLMConfigError,
    LLMError,
    LLMHTTPError,
    LLMProvider,
    LLMStructuredError,
)
from contextos.llm.fake import FakeLLM
from contextos.llm.factory import provider_from_profile
from contextos.llm.openai_compat import OpenAICompatProvider

__all__ = [
    "LLMProvider",
    "OpenAICompatProvider",
    "FakeLLM",
    "provider_from_profile",
    "LLMError",
    "LLMConfigError",
    "LLMStructuredError",
    "LLMHTTPError",
]
