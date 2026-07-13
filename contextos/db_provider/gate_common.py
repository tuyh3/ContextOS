"""方言中立的安全闸门公共件(spec 2026-07-10 附录 F, 硬约束 #4 家族)。

PRODUCTION_KEYWORDS 的单一 SSOT 从 oracle_gate 上提至此(F.3), oracle_gate
反向 import 保持对外符号不变。MySQL 等新方言的实例白名单闸在此实现:
与 Oracle 的差异是"拿什么串当身份"——Oracle 的 TNS 名沿用机房命名规范
(关键词有真实信号), 而新方言的 alias 是用户在 profile 里自起的(起个中性名
就绕过了), 所以必须 alias + host + 每个库名三类串一起扫(F.1)。fail-closed:
任何配置异常态(空白名单/零库)一律拒绝, 不静默放行。
"""
from __future__ import annotations

from collections.abc import Iterable

PRODUCTION_KEYWORDS = ("PROD", "PRD", "LIVE", "MASTER", "RELEASE")


class DbSafetyError(Exception):
    """任何方言的实例/SQL 安全闸拒绝。OracleSafetyError 是其子类(存量 except 不破)。"""


def _reject_production_keyword(kind: str, value: str) -> None:
    upper = (value or "").upper()
    for kw in PRODUCTION_KEYWORDS:
        if kw in upper:
            raise DbSafetyError(
                f"{kind} {value!r} contains production keyword {kw!r}; refused"
            )


def assert_instance_is_test_only(
    *,
    alias: str,
    host: str,
    databases: Iterable[str],
    allowed_aliases: Iterable[str],
) -> None:
    """MySQL 等直连方言的白名单闸(附录 F.1)。

    三类串(alias/host/每个库名)逐一过 production-keyword 扫描, 任一命中即拒;
    alias 必须在 profile 白名单内精确匹配。执行点 = 客户端构造 + profile
    load 期 validator 双份(与 Oracle 对齐)。
    """
    db_list = [d for d in databases if (d or "").strip()]
    if not db_list:
        raise DbSafetyError(f"instance {alias!r} has no databases configured; refused")
    _reject_production_keyword("alias", alias)
    _reject_production_keyword("host", host)
    for db in db_list:
        _reject_production_keyword("database", db)
    allowed_list = list(allowed_aliases)
    if alias not in allowed_list:
        raise DbSafetyError(
            f"alias {alias!r} not in allowed instance aliases {allowed_list!r}"
        )
