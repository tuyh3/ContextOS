"""text 源适配器:passthrough + 轻归一。CLI 直接敲文字走这条。"""
from __future__ import annotations

import re

from contextos.requirement.adapters.base import AdapterResult, parse_failure, register

_MULTI_BLANK = re.compile(r"\n\s*\n\s*\n+")


def parse_text(raw_input: str) -> AdapterResult:
    text = (raw_input or "").strip()
    if not text:
        return parse_failure("空文本")
    # 折叠 3+ 连续空行为 1 个空行(轻归一,不改内容)
    text = _MULTI_BLANK.sub("\n\n", text)
    return AdapterResult(raw_text=text)


register("text", parse_text)
