"""OpenAICompatProvider — OpenAI 兼容 /chat/completions HTTP client。

一套覆盖 anthropic 兼容端点 / openai / 本地 Qwen(OpenAI 兼容 server),
无专用 SDK 依赖,只用已在依赖里的 requests。
"""
from __future__ import annotations

import requests

from contextos.llm.base import LLMHTTPError, LLMProvider


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        temperature: float = 0.0,
        timeout: int = 60,
        max_retries: int = 2,  # 预留:HTTP 层重试(本 task 不实现重试循环,structured() 已有语义重试)
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._timeout = timeout
        self._max_retries = max_retries

    def complete(self, prompt, *, system=None, temperature=None, max_tokens=None) -> str:
        messages = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature if temperature is None else temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=self._timeout)
        except requests.RequestException as e:
            raise LLMHTTPError(f"LLM request failed: {e}") from e

        if resp.status_code != 200:
            raise LLMHTTPError(
                f"LLM endpoint returned {resp.status_code}: {resp.text[:500]}"
            )
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMHTTPError(f"malformed LLM response: {e}") from e
