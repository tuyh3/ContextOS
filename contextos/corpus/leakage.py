"""LeakageGate: 语料泄漏闸门 —— 正则排除"点名具体改动文件"的文档(change-log / gold 类)。

这类文档直接说了哪个文件改了, 等于把答案抄给检索, 故走客户配置的 leakage_exclude_regex
正则排除。内部 provider 与 host 旁路共享同一 gate。

历史: 旧红线 #2 曾在此硬编码 ".xlsx/.xls 后缀永拒"(FPA 数据资产不进语料)。2026-06-10 用户
裁决移除该 blanket —— FPA xlsx 不再当永禁项, xlsx 升为可选语料(materialization 路径按 v2
计划另接, 见 docs/讨论/2026-06-06-红线2改造-FPA参考源与xlsx通用语料.md)。后缀拒机制保留为
通用可选项(deny_suffixes), 仅默认清空。
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_DENY_SUFFIXES: tuple[str, ...] = ()  # 2026-06-10: 移除 FPA xlsx blanket(旧红线 #2); 后缀拒机制保留, 默认空


class LeakageGate:
    def __init__(
        self,
        exclude_regexes: Iterable[str] | None = None,
        deny_suffixes: tuple[str, ...] = _DENY_SUFFIXES,
    ) -> None:
        self._patterns = [re.compile(r) for r in (exclude_regexes or [])]
        self._deny_suffixes = tuple(s.lower() for s in deny_suffixes)

    def is_allowed(self, rel_path: str) -> bool:
        if self._deny_suffixes and rel_path.lower().endswith(self._deny_suffixes):
            return False
        return not any(p.search(rel_path) for p in self._patterns)
