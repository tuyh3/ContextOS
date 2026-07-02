"""dedupe_key / case_id 计算(spec Appendix A)。

dedupe_key = normalize(phenomenon_signature) + "\x1f" + mechanism_tag  (去重比对找候选)
case_id    = sha256(dedupe_key + "\x1f" + normalize(confirmed_root_cause))  (加 root_cause 区分)
  -> 同 signature+mechanism 但 root_cause 不同的 case 各有独立 id 不撞(differential + conflict 共存)。
normalize: 小写 + 连续空白折叠成单空格 + 首尾 strip(归一抗格式抖动)。
"""
from __future__ import annotations

import hashlib
import re

_WS_RE = re.compile(r"\s+")
_SEP = "\x1f"   # ASCII unit separator: 不出现在正常文本里, 防字段拼接歧义


def normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def compute_dedupe_key(phenomenon_signature: str, mechanism_tag: str) -> str:
    return normalize(phenomenon_signature) + _SEP + (mechanism_tag or "")


def compute_case_id(dedupe_key: str, confirmed_root_cause: str) -> str:
    raw = dedupe_key + _SEP + normalize(confirmed_root_cause)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
