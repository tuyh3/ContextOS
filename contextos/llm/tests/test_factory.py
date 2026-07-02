"""provider_from_profile:override 优先 / 缺 base_url|model 报 LLMConfigError / 缺 env 报错。"""
from __future__ import annotations

import pytest

from contextos.llm import FakeLLM, OpenAICompatProvider, provider_from_profile
from contextos.llm.base import LLMConfigError


class _LLMCfg:
    def __init__(self, **kw) -> None:
        self.provider = kw.get("provider", "claude")
        self.api_key_env = kw.get("api_key_env", "TEST_LLM_KEY")
        self.base_url = kw.get("base_url")
        self.model = kw.get("model")
        self.temperature = kw.get("temperature", 0.0)
        self.timeout_seconds = kw.get("timeout_seconds", 60)
        self.max_retries = kw.get("max_retries", 2)


class _Profile:
    def __init__(self, llm) -> None:
        self.llm = llm


def test_override_short_circuits(monkeypatch) -> None:
    fake = FakeLLM(responses=["x"])
    prof = _Profile(_LLMCfg())
    assert provider_from_profile(prof, override=fake) is fake


def test_builds_openai_compat_when_config_complete(monkeypatch) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "sk-abc")
    prof = _Profile(_LLMCfg(base_url="https://host/v1", model="m1"))
    p = provider_from_profile(prof)
    assert isinstance(p, OpenAICompatProvider)


def test_missing_base_url_or_model_raises(monkeypatch) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "sk-abc")
    prof = _Profile(_LLMCfg(base_url=None, model=None))
    with pytest.raises(LLMConfigError):
        provider_from_profile(prof)


def test_missing_api_key_env_raises(monkeypatch) -> None:
    monkeypatch.delenv("TEST_LLM_KEY", raising=False)
    prof = _Profile(_LLMCfg(base_url="https://host/v1", model="m1"))
    with pytest.raises(LLMConfigError):
        provider_from_profile(prof)
