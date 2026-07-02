"""Oracle 三道闸门:白名单 / production-keyword / 只读 SQL。

抽自原 sqlcl_mcp.py。其它代码请只 import 本模块。Profile-aware helper
`assert_profile_oracle_ok` 在 Task 8 由 sqlcl_mcp.py 重新封一层。

Known limitations (intentional, Hard Constraint #4 "宁可错杀"):
- String literals NOT stripped before forbidden-keyword scan, so a legit query
  like `SELECT * FROM audit_log WHERE action = 'DELETE'` is rejected. v1 scope
  (ALL_TABLES/ALL_COLUMNS/ALL_SOURCE metadata) is unaffected; audit/lineage
  queries that read DML strings as data are v2 concern — handle via Profile-
  aware extension or whitelist-per-table mechanism then.
- `_FORBIDDEN_KEYWORDS` matches `\\b...\\b`, so quoted identifier `"REVOKE"`
  also triggers — theoretical false positive, no realistic Oracle query uses
  these as column names.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

PRODUCTION_KEYWORDS = ("PROD", "PRD", "LIVE", "MASTER", "RELEASE")

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--[^\n]*")
_READONLY_PREFIX = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(?:DELETE|UPDATE|INSERT|DROP|MERGE|CREATE|ALTER|GRANT|"
    r"REVOKE|EXEC|EXECUTE|CALL|BEGIN)\b"
    # TRUNCATE 只在 `TRUNCATE TABLE` / `TRUNCATE CLUSTER` 写语句上下文拦截; 裸 TRUNCATE
    # 是合法只读子句关键字(如 LISTAGG ... ON OVERFLOW TRUNCATE), 不再误伤(2026-06-07)。
    r"|\bTRUNCATE\s+(?:TABLE|CLUSTER)\b",
    re.IGNORECASE,
)


class OracleSafetyError(Exception):
    """Raised when a TNS / SQL violates POC test-instance safety rails."""


def assert_tns_is_test_only(tns: str, allowed: Iterable[str]) -> None:
    upper = tns.upper()
    for kw in PRODUCTION_KEYWORDS:
        if kw in upper:
            raise OracleSafetyError(
                f"TNS {tns!r} contains production keyword {kw!r}; refused"
            )
    allowed_list = list(allowed)
    if tns not in allowed_list:
        raise OracleSafetyError(
            f"TNS {tns!r} not in allowed_instances {allowed_list!r}"
        )


def assert_query_is_readonly(sql: str) -> None:
    stripped = _COMMENT_BLOCK.sub(" ", sql)
    stripped = _COMMENT_LINE.sub(" ", stripped)
    if ";" in stripped.strip().rstrip(";"):
        raise OracleSafetyError("multi-statement SQL refused; one read-only stmt only")
    if not _READONLY_PREFIX.match(stripped):
        raise OracleSafetyError(
            "SQL must start with SELECT or WITH; got " + stripped.lstrip()[:40]
        )
    forbidden = _FORBIDDEN_KEYWORDS.search(stripped)
    if forbidden:
        raise OracleSafetyError(
            f"forbidden keyword {forbidden.group()!r} in SQL; read-only only"
        )
