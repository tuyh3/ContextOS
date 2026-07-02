"""evidence_pointers 白名单前缀 fail-closed 校验(spec Appendix B MUST)。

只收三类(对齐现有约束):
  fqn:<点号FQN>      -- middleware._FQN_RE 同口径(点号形, 拒 #|;|& / 路径穿越 / 超长)
  table:OWNER.TABLE  -- 大写下划线 OWNER.TABLE(search_sql 验真口径)
  config:<key.path>  -- lookup_config 的 key.path(点分小写/数字/下划线/连字符)
拒裸 SQL / 文件路径 / URL / 行数据 / literal value / 其它前缀。任一非法 -> 整调用 reject。
"""
from __future__ import annotations

import re

from contextos.mcp_server.middleware import _FQN_RE

_TABLE_RE = re.compile(r"^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$")
_CONFIG_RE = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$")
_FQN_MAX = 512


class EvidencePointerError(ValueError):
    """evidence_pointer 不匹配白名单前缀 / 形态非法。"""


def _valid_one(ptr: str) -> bool:
    if not isinstance(ptr, str) or not ptr:
        return False
    if ptr.startswith("fqn:"):
        body = ptr[4:]
        return len(body) <= _FQN_MAX and bool(_FQN_RE.match(body))
    if ptr.startswith("table:"):
        return bool(_TABLE_RE.match(ptr[6:]))
    if ptr.startswith("config:"):
        return bool(_CONFIG_RE.match(ptr[7:]))
    return False


def validate_pointers(pointers: list[str]) -> None:
    """每项必须匹配白名单前缀正则; 任一非法 raise EvidencePointerError(fail-closed)。"""
    for ptr in pointers:
        if not _valid_one(ptr):
            raise EvidencePointerError(
                f"evidence_pointer {ptr!r} not allowed; "
                "只收 fqn:<点号FQN> / table:OWNER.TABLE / config:<key.path>"
            )
