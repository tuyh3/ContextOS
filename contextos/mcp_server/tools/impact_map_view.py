"""build_impact_map MCP 视图层纯函数(spec 2026-06-17 §6/§7/§8.5)。

不依赖 app_ctx / 重资源, 对 model_dump 出的 dict 操作, 可纯单测。
compact_evidence(B3 紧凑) / summarize(B4 摘要) / verify_no_sensitive(出口脱敏)。
"""
from __future__ import annotations

from contextos.impact_map.enums import KIND_CONFIG_DIMENSION, KIND_SQL_DIMENSION

_METHOD_KINDS = frozenset({
    "METHOD", "CLASS", "INTERFACE", "FIELD", "API_ENTRY", "JOB", "BATCH", "MSG",
})
_TIER_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _group_of(kind: str) -> str:
    if kind in _METHOD_KINDS:
        return "method"
    if kind in KIND_SQL_DIMENSION:
        return "sql_table"
    if kind in KIND_CONFIG_DIMENSION:
        return "config"
    return "other"   # kind==OTHER(含 OBJECT_DEPENDENCY / 未知); 仅用于分组上限, 不进质量轴


def _meta(item: dict) -> dict:
    return item.get("metadata") or {}


def _is_folded(item: dict) -> bool:
    return bool(_meta(item).get("folded"))


def _consensus(item: dict) -> int:
    try:
        return int(_meta(item).get("consensus_count") or 0)
    except (TypeError, ValueError):
        return 0


def _is_strong(item: dict, n: int) -> bool:
    return item.get("confidence_tier") == "HIGH" or _consensus(item) >= n


def _sort_key(item: dict) -> tuple[int, int, float]:
    return (_TIER_RANK.get(item.get("confidence_tier") or "", 3),
            -_consensus(item),
            -float(item.get("confidence") or 0.0))


def _cap_per_group(items: list, top_n: int) -> list:
    groups: dict[str, list] = {}
    for it in items:
        groups.setdefault(_group_of(it.get("kind") or ""), []).append(it)
    out: list = []
    for g_items in groups.values():
        out.extend(sorted(g_items, key=_sort_key)[:top_n])
    return out


def compact_evidence(evidence_items: list, *, consensus_min_bridges: int,
                     top_n: int) -> tuple[list, bool]:
    """返回 (紧凑 evidence, 是否走了空核兜底)。

    正常分支: 排 folded -> 强核线(HIGH 或 consensus>=N)-> 每维度 top_n。
    空核兜底: 有 evidence 但无强核时, 从完整集(含 folded)取每维度 top_n, 防"假装啥也没找到"。
    真无 evidence(evidence_items 空)-> ([], False): 不是空核兜底(真没有 != 弱线索被折叠)。
    """
    unfolded = [it for it in evidence_items if not _is_folded(it)]
    strong = [it for it in unfolded if _is_strong(it, consensus_min_bridges)]
    if strong:
        return _cap_per_group(strong, top_n), False
    if not evidence_items:
        return [], False   # 真无证据, 非空核兜底
    return _cap_per_group(list(evidence_items), top_n), True


# ---------------------------------------------------------------------------
# Task 6: summarize(B4 摘要) + verify_no_sensitive(出口脱敏)
# ---------------------------------------------------------------------------

# 非定位 source(重排器 / 佐证桥): by_domain_source 排除这些, 只留"谁真定位了候选"。
# 与 assemble._FALLBACK_SOURCES / _DOMAIN_WORKER 同源口径("谁是真定位者"): 改一处先核对另一处,
# 防 by_domain_source 与 core fallback_only 判定漂移(spec §5.3 + §6)。
NON_LOCATING_SOURCES = frozenset({"llm-rerank", "rag-cross-encoder", "rag-bi-encoder"})

# field_coverage producer 能力注册表(spec §6): v1 哪些顶层 list 字段有填充 producer。
# v1.x 真加 producer 时翻这里对应项即可, 不留硬编码假象。
_FIELD_PRODUCERS = {
    "candidate_entrypoints": True,    # assemble 按 _ENTRYPOINT_KIND_MAP 条件填
    "modules_touched": False,         # v1 assemble 无填充逻辑
    "relations": False,
}

RECOMMENDED_USE_BASE = (
    "默认视图=多桥共识强核(HIGH 或 >=N 桥共识, N=consensus_min_bridges)。"
    "代码维度通常证据最强(以 dimension_quality 标注为准);config 标 fallback_only 时为 "
    "grep 兜底命中, 仅作线索勿当定位结论。被折叠的弱线索 + 单桥候选已全部保留, 传 full=true "
    "可展开, 供低阈值消费 / 审计 / 召回。本工具给候选 + 证据, 不替你下结论;"
    "判断请结合 evidence_refs.source、confidence_tier 与 dimension_quality。"
)
EMPTY_CORE_NOTE = " 注意: 本次无强核, 默认视图含被折叠弱线索, 建议 full=true 核对。"
NO_EVIDENCE_NOTE = "本次三维均未产出候选 evidence(非紧凑截断, 全量同样为空)。可检查需求是否在范围内 / 索引是否已构建。"

# 值承载字段: 出口绝不带非空值(红线 #9 家族; spec §8.5)。这是"敏感值"的结构化载体 ——
# value_raw=配置原始值 / content_raw=audit 原文 quote / content_summary=原文摘要。
# 按字段名(值落点)判, 不扫 key 名/自由文本: 配置 key 名字面可含 password(合法标识符非值), 避免误报。
SENSITIVE_VALUE_KEYS = frozenset({"value_raw", "content_raw", "content_summary"})


def _count_by(items: list, key: str) -> dict:
    out: dict = {}
    for it in items:
        k = it.get(key)
        if k is not None:
            out[k] = out.get(k, 0) + 1
    return out


def _count_sources(items: list, *, exclude: frozenset = frozenset()) -> dict:
    out: dict = {}
    for it in items:
        for ref in it.get("evidence_refs") or []:
            src = ref.get("source")
            if src is None or src in exclude:
                continue
            out[src] = out.get(src, 0) + 1
    return out


def _field_coverage(impact: dict) -> dict:
    out: dict = {}
    for field, has_producer in _FIELD_PRODUCERS.items():
        if not has_producer:
            out[field] = "not_populated_in_v1"
        else:
            out[field] = "populated" if impact.get(field) else "none_found"
    return out


def summarize(impact: dict, *, returned: int, full: bool,
              empty_core_fallback: bool) -> dict:
    """B4 摘要块。计数全部基于完整集(impact['evidence_items'] 此时仍是全量)。"""
    evidence = impact.get("evidence_items") or []
    total = len(evidence)
    truncated = (not full) and returned < total
    if total == 0:
        recommended = NO_EVIDENCE_NOTE   # 真无 evidence: 不引导 full=true(全量同空)
    else:
        recommended = RECOMMENDED_USE_BASE + (EMPTY_CORE_NOTE if empty_core_fallback else "")
    summary = {
        "evidence_total": total,
        "returned": returned,
        "truncated": truncated,
        "by_tier": _count_by(evidence, "confidence_tier"),
        "by_kind": _count_by(evidence, "kind"),
        "by_domain_source": _count_sources(evidence, exclude=NON_LOCATING_SOURCES),
        "by_source_ref": _count_sources(evidence),
        "dimension_quality": dict(impact.get("dimension_quality") or {}),
        "field_coverage": _field_coverage(impact),
        "recommended_use": recommended,
    }
    if truncated:
        summary["how_to_get_full"] = (
            "重新调用并传 full=true 取全部 evidence_items(含被折叠 / 单桥弱线索)")
    return summary


def verify_no_sensitive(obj) -> None:
    """递归断言值承载字段(SENSITIVE_VALUE_KEYS)无非空值。fail-closed(发现即抛, 绝不外泄)。

    只查"值会落进来"的字段(value_raw/content_raw/content_summary), 不扫 key 名/自由文本 ——
    配置 key 名字面可含 password(合法标识符非凭据值), 朴素 token 扫描会误报。
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in SENSITIVE_VALUE_KEYS and v:
                raise ValueError(f"sensitive value leaked at key {k!r}")
            verify_no_sensitive(v)
    elif isinstance(obj, list):
        for it in obj:
            verify_no_sensitive(it)
