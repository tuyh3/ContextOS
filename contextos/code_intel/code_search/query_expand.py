"""搜索词扩展(04 §3 第1步前置)。

专治 02 把业务概念拼成"过具体复合名"导致 workspaceSymbol 撞不上真符号:
手测 2026-06-01 实证 02 产 `DynamicChargingBatch`,但真类是 `DynamicChargingSVImpl`,
`DynamicChargingBatch` 不是它的前缀/子序列 -> 0 命中;而裸核心 `DynamicCharging`
能撞上正确的 4 个类。

策略: 原词 + 逐步丢尾部词的前缀(驼峰边界 / 下划线切词),只保留 >= 2 词的前缀
(丢到 1 词如 "Dynamic" / "Sms" 太宽,噪音大,留给 RAG/字典桥)。最多 max_prefixes 个。
命中强度由调用方对【原词】算(经子查询找到的算模糊,不虚高),本模块只产查询串。
"""
from __future__ import annotations


def _word_starts(term: str) -> list[int]:
    """返回 term 中每个"词"的起始下标(0 + 驼峰边界 + 下划线后)。

    边界规则:
    - 下划线后第一个非下划线字符(SMS_REMINDER -> SMS | REMINDER)
    - 小写/数字后接大写(DynamicCharging -> Dynamic | Charging)
    - 大写缩写尾(连续大写后接 大写+小写: SMSReminder -> SMS | Reminder)
    """
    starts = [0]
    for i in range(1, len(term)):
        c, prev = term[i], term[i - 1]
        if c == "_":
            continue
        if prev == "_":
            starts.append(i)
        elif c.isupper() and (prev.islower() or prev.isdigit()):
            starts.append(i)
        elif (
            c.isupper()
            and prev.isupper()
            and i + 1 < len(term)
            and term[i + 1].islower()
        ):
            starts.append(i)
    return starts


def expand_search_term(term: str, *, max_prefixes: int = 2) -> list[str]:
    """原词 + 最多 max_prefixes 个"丢尾词"前缀(>= 2 词,长在前)。

    DynamicChargingBatch        -> [DynamicChargingBatch, DynamicCharging]
    DynamicChargingBatchOperation -> [..., DynamicChargingBatch, DynamicCharging]
    SmsReminder / SMS_REMINDER  -> [原词]  (2 词,丢到 1 词太宽,不拆)
    """
    out = [term]
    if not term:
        return out
    starts = _word_starts(term)
    total = len(starts)
    # k = 前缀词数, 从 total-1 递减到 2(长前缀在前)
    for k in range(total - 1, 1, -1):
        if len(out) - 1 >= max_prefixes:
            break
        prefix = term[: starts[k]].rstrip("_")
        if prefix and prefix != term and prefix not in out:
            out.append(prefix)
    return out
