"""新增专用 PII gate(spec Appendix B MUST, 红线 #9 家族)。

为什么不复用 config_dim/sensitive.py:
- verify_no_sensitive 只查特定字段名;sanitize_text 要外部给 patterns —— 都不扫自由文本 PII。
本 gate 扫 record_confirmed_case 的 4 检索字段(会进 RAG markdown 被全文搜), 命中 MSISDN /
email / 手机 / 姓名 / 凭据 / value_raw 形态即 raise PiiGateError, 要求 host 先中性化重交。

姓名走**保守标签形态**(`客户姓名 / 姓名 / name` 标签后跟值)而非裸中文名匹配:
裸中文名正则会高误伤(业务描述里的人名/产品名/术语三字串都会撞), 收益不抵代价;
标签形态精确锚定"这是被当作姓名字段填的值", 命中率低误伤、漏掉裸名由 meta 枚举层 +
人工 review 兜底(spec Appendix E [meta 无 PII] + human-gated 回写闸)。
"""
from __future__ import annotations

import re

# 只扫这 4 个会进 RAG markdown 的检索字段(spec Appendix B)。
_SCANNED_FIELDS = (
    "phenomenon_signature", "confirmed_root_cause", "decisive_data_note", "search_terms",
)

# 各 PII 形态(中文电信场景 + 通用)。锚定形态, 非裸数字串(避免误伤普通数值)。
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # email
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    # MSISDN: 国际码 86 + 11 位, 或裸 11 位中国手机号(1[3-9] 开头)
    ("msisdn", re.compile(r"\b(?:86)?1[3-9]\d{9}\b")),
    # 凭据形态: password/pwd/secret/token/credential = / : value
    ("credential", re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|token|credential|api[_-]?key)\b\s*[=:]\s*\S+")),
    # value_raw 形态: 字段名 value_raw 带值(脱敏前的原始值不该进案例库)
    ("value_raw", re.compile(r"(?i)\bvalue_raw\b\s*[=:]\s*\S+")),
    # 姓名: 保守标签形态(客户姓名/姓名/name 标签后跟 :/:/=/空白 + 值)。
    # 不裸匹配中文名(高误伤业务术语/产品名), 只拒明确当姓名字段填的值。
    ("name", re.compile(r"(?i)(?:客户姓名|姓名|name)\s*[:：=]\s*\S+")),
)


class PiiGateError(ValueError):
    """检索字段含 PII / 凭据 / value_raw 形态; 要求 host 先中性化重交。"""


def assert_no_pii(fields: dict[str, object]) -> None:
    """校验 fields 里的 4 检索字段无 PII 形态。命中即 raise PiiGateError(整调用 reject)。

    fields 值可为 str / None / ""; 非 str 或空跳过。只扫 _SCANNED_FIELDS, 其它键忽略。
    """
    for name in _SCANNED_FIELDS:
        val = fields.get(name)
        if not isinstance(val, str) or not val:
            continue
        for label, rx in _PII_PATTERNS:
            if rx.search(val):
                raise PiiGateError(
                    f"PII-like content ({label}) in field {name!r}; "
                    "中性化后重交(不收 MSISDN/email/手机/姓名/凭据/value_raw)"
                )
