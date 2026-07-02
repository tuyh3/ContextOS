"""02 RequirementBreakdown -> 04 CodeSearchQuery 归一(README §5 契约 02->04)。

字段名对齐:02 的 candidate_code_names(term/kind)-> 04 search_terms;
matched_capabilities 取置信最高的一个当 matched_capability(04 §7 context)。
sub_project_hints 02 v1 不产,留空(04 §7 可空)。
"""
from __future__ import annotations

from contextos.code_intel.code_search.schema import CodeSearchQuery, SearchTerm
from contextos.requirement.schema import RequirementBreakdown


def breakdown_to_query(breakdown: RequirementBreakdown) -> CodeSearchQuery:
    terms = [
        SearchTerm(term=c.term, kind=c.kind)
        for c in breakdown.candidate_code_names
        if c.term.strip()
    ]
    capability = ""
    if breakdown.matched_capabilities:
        top = max(breakdown.matched_capabilities, key=lambda m: m.confidence)
        capability = top.capability
    # matched_capability / sub_project_hints 携带进 query 但 v1 种子主线(seeds/provider)
    # 暂不消费 —— 留给 Plan 04b 的范围缩窄(04 §8 剪枝)+ 08 编排上下文,不是被忽略的 hint。
    return CodeSearchQuery(
        search_terms=terms,
        matched_capability=capability,
        sub_project_hints=[],
    )
