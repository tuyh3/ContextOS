"""代码搜索的 tool 形态适配(Plan 10 §证据 tool)。

provider.py 吃 02 RequirementBreakdown(走整条 02->04 归一 + 失败传播),供 08
编排用;本模块是【tool 形态】= 单 query 字符串直查符号,返回纯 dict list,给
MCP/CLI/Python lib 共用(core tools.py 只查、返纯 dict,不碰 MCP 协议)。

复用 find_seeds(seeds.py):把 query 包成 SearchTerm 撞真符号。返回
[{target, kind, score, file, name_match}]。kind 参数非空时按 01 §3.1 Kind 过滤。
"""
from __future__ import annotations

from contextos.code_intel.code_search.schema import SearchTerm
from contextos.code_intel.code_search.seeds import SymbolSearcher, find_seeds


def search_code_query(searcher: SymbolSearcher, *, query: str, kind: str = "") -> list[dict]:
    """单 query 查符号(tool 形态)。

    query: 自由文本符号名;空 / 全空白 -> 不打 JDT, 返回 []。
    kind:  非空时只保留该 01 §3.1 Kind(CLASS/METHOD/FIELD/...)的候选。
    返回:  [{target, kind, score, file, name_match}](纯 JSON 友好标量)。
    """
    q = query.strip()
    if not q:
        return []

    seeds = find_seeds(searcher, [SearchTerm(term=q, kind="other")])

    rows: list[dict] = []
    for c in seeds:
        if kind and c.kind != kind:
            continue
        name_match = float(c.signals.get("name_match_strength", 0.0))
        rows.append({
            "target": c.target,
            "kind": c.kind,
            # tool 形态不叠 source_confidence(那是 08 provider 层的事);
            # score = 命中强度本身, 让上层(MCP/CLI)自行解释。
            "score": name_match,
            "file": str(c.signals.get("file", "")),
            "name_match": name_match,
        })
    return rows
