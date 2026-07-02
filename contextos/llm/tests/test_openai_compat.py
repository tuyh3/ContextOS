"""OpenAICompatProvider:请求构造 + 响应解析 + 错误处理(全程 monkeypatch requests.post,不打网络)。"""
from __future__ import annotations

import pytest

from contextos.llm.base import LLMHTTPError
from contextos.llm.openai_compat import OpenAICompatProvider


class _FakeResp:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    @property
    def text(self) -> str:
        return str(self._payload)


def _ok_payload(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _make(monkeypatch, resp: _FakeResp, capture: dict) -> OpenAICompatProvider:
    def fake_post(url, *, headers=None, json=None, timeout=None):
        capture["url"] = url
        capture["headers"] = headers
        capture["json"] = json
        capture["timeout"] = timeout
        return resp

    monkeypatch.setattr("contextos.llm.openai_compat.requests.post", fake_post)
    return OpenAICompatProvider(
        base_url="https://host/v1", model="m1", api_key="sk-test",
        temperature=0.0, timeout=30, max_retries=0,
    )


def test_complete_posts_to_chat_completions_and_returns_content(monkeypatch) -> None:
    cap: dict = {}
    p = _make(monkeypatch, _FakeResp(200, _ok_payload("hello world")), cap)
    out = p.complete("hi there", system="be terse")
    assert out == "hello world"
    assert cap["url"] == "https://host/v1/chat/completions"
    assert cap["headers"]["Authorization"] == "Bearer sk-test"
    assert cap["json"]["model"] == "m1"
    # system + user 两条 message
    roles = [m["role"] for m in cap["json"]["messages"]]
    assert roles == ["system", "user"]
    assert cap["json"]["messages"][1]["content"] == "hi there"
    assert cap["timeout"] == 30


def test_complete_omits_system_message_when_none(monkeypatch) -> None:
    cap: dict = {}
    p = _make(monkeypatch, _FakeResp(200, _ok_payload("x")), cap)
    p.complete("only user")
    roles = [m["role"] for m in cap["json"]["messages"]]
    assert roles == ["user"]


def test_complete_raises_llmhttperror_on_non_200(monkeypatch) -> None:
    cap: dict = {}
    p = _make(monkeypatch, _FakeResp(500, {"error": "boom"}), cap)
    with pytest.raises(LLMHTTPError):
        p.complete("x")


def test_complete_raises_on_malformed_response(monkeypatch) -> None:
    cap: dict = {}
    p = _make(monkeypatch, _FakeResp(200, {"unexpected": "shape"}), cap)
    with pytest.raises(LLMHTTPError):
        p.complete("x")


def test_temperature_override_wins_over_default(monkeypatch) -> None:
    cap: dict = {}
    p = _make(monkeypatch, _FakeResp(200, _ok_payload("y")), cap)
    p.complete("x", temperature=0.7)
    assert cap["json"]["temperature"] == 0.7


def test_max_tokens_absent_by_default_present_when_supplied(monkeypatch) -> None:
    cap: dict = {}
    p = _make(monkeypatch, _FakeResp(200, _ok_payload("z")), cap)
    p.complete("x")
    assert "max_tokens" not in cap["json"]
    p.complete("x", max_tokens=256)
    assert cap["json"]["max_tokens"] == 256


def test_transport_exception_becomes_llmhttperror(monkeypatch) -> None:
    # 网络层失败(连不上 / 超时)也必须收敛成 LLMHTTPError,不漏 requests 原生异常
    import requests as _requests

    def boom(url, *, headers=None, json=None, timeout=None):
        raise _requests.ConnectionError("refused")

    monkeypatch.setattr("contextos.llm.openai_compat.requests.post", boom)
    p = OpenAICompatProvider(base_url="https://host/v1", model="m1", api_key="sk-test")
    with pytest.raises(LLMHTTPError):
        p.complete("x")
