# contextos/orchestrator/pipeline.py
"""08 主流程:run_impact_analysis(纯编排,不调 LLM 自己——breakdown 由调用方先跑)。

02 breakdown(调用方传)-> stage1 cheap 桥(fail-safe miss)-> RAG 投影 + pool/dedup/sort 非 RAG
-> stage2 07 rerank(NullLookup,registry 闭包已注入)-> corroborate -> fold -> assemble ImpactMap。
"""
from __future__ import annotations

from contextos.orchestrator.assemble import assemble_impact_map
from contextos.orchestrator.corroboration import CorroboratedCandidate, corroborate, score_bridge
from contextos.orchestrator.folding import apply_folding
from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.orchestrator.rag_projection import build_rag_projection
from contextos.orchestrator.registry import ProviderRegistry
from contextos.profile.schema import CorroborationConfig


def _run_cheap(registry: ProviderRegistry, breakdown) -> dict[str, ProviderResult]:
    results: dict[str, ProviderResult] = {}
    for bridge in registry.cheap_bridges:
        try:
            results[bridge.worker_name] = bridge.run(breakdown)
        except Exception as exc:                       # §5.1:provider 失败当 miss,不阻塞
            results[bridge.worker_name] = ProviderResult.miss(
                bridge.worker_name, f"bridge_error:{type(exc).__name__}")
    return results


def _pool_for_rerank(cheap_results: dict[str, ProviderResult]) -> list[ProviderCandidate]:
    """非 RAG cheap 候选去重(按 (kind, target),保最高 cheap score),按 score 降序(级联:好的先,07 cap 留 top)。"""
    by_key: dict[tuple[str, str], tuple[float, ProviderCandidate]] = {}
    for worker, res in cheap_results.items():
        if worker == "rag":
            continue                                   # RAG 文档不当 impact 候选(投影特例 G5)
        for c in res.candidates:
            s = score_bridge(worker, c.signals)
            key = (c.kind, c.target)                    # 身份 = (kind, target)(同表跨维不互吞, review HIGH 1)
            prev = by_key.get(key)
            if prev is None or s > prev[0]:
                by_key[key] = (s, c)
    ranked = sorted(by_key.values(), key=lambda t: t[0], reverse=True)
    return [c for _s, c in ranked]


def run_impact_analysis(breakdown, registry: ProviderRegistry, *,
                        corroboration_config: CorroborationConfig | None = None):
    """编排一条需求的 Impact Map。返回 (ImpactMap, ctx);ctx 供 artifact 落盘。"""
    cfg = corroboration_config or CorroborationConfig()

    if breakdown.assessment == "rejected":
        return assemble_impact_map(breakdown, [], cfg.consensus_min_bridges), {
            "cheap_results": {}, "rerank_result": None, "corrobs": []}

    cheap_results = _run_cheap(registry, breakdown)
    rag_res = cheap_results.get("rag")
    rag_proj = build_rag_projection(rag_res.candidates if rag_res else [])

    pool = _pool_for_rerank(cheap_results)
    if registry.rerank_bridge is not None:
        try:
            rerank_result = registry.rerank_bridge.run(breakdown, pool)
        except Exception as exc:                       # §5.1:07 失败当 miss
            rerank_result = ProviderResult.miss(
                registry.rerank_bridge.worker_name, f"bridge_error:{type(exc).__name__}")
    else:
        rerank_result = ProviderResult.miss("llm_rerank", "no_rerank_bridge")

    corrobs: list[CorroboratedCandidate] = corroborate(cheap_results, rerank_result, rag_proj, cfg)
    apply_folding(corrobs, cfg)
    impact = assemble_impact_map(breakdown, corrobs, cfg.consensus_min_bridges)
    return impact, {"cheap_results": cheap_results, "rerank_result": rerank_result, "corrobs": corrobs}


def run_and_persist(breakdown, registry: ProviderRegistry, *, raw_input: str = "",
                    corroboration_config: CorroborationConfig | None = None,
                    artifact_root=None, now=None, short_hash: str = "run"):
    """run_impact_analysis + 可选 §6 run artifact 落盘。返回 (ImpactMap, ctx)。"""
    from datetime import datetime
    started = now or datetime.now()
    impact, ctx = run_impact_analysis(breakdown, registry,
                                      corroboration_config=corroboration_config)
    ended = now or datetime.now()                       # now 注入时 started==ended(测试确定);真跑各取实时
    if artifact_root is not None:
        from contextos.orchestrator.artifact import make_run_id, write_run_artifact
        run_id = make_run_id(impact.requirement_summary, now=started, short_hash=short_hash)
        # errors 合并 cheap + rerank miss(review MEDIUM 3)
        errors = [f"{w}:{r.miss_reason}" for w, r in ctx["cheap_results"].items() if r.miss_reason]
        rr = ctx["rerank_result"]
        if rr is not None and rr.miss_reason:
            errors.append(f"{rr.worker_name}:{rr.miss_reason}")
        # summary 全 §6 字段(review MEDIUM 3):状态/版本/开始/结束/用时/token(v1 未串 token 计量 -> null)
        summary_meta = {
            "status": "rejected" if breakdown.assessment == "rejected" else "completed",
            "version": impact.version,
            "assessment": breakdown.assessment,
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "duration_ms": round((ended - started).total_seconds() * 1000, 1),
            "total_tokens": None,
            "evidence_count": len(impact.evidence_items),
            "folded_count": sum(1 for e in impact.evidence_items if e.metadata.get("folded")),
        }
        write_run_artifact(
            artifact_root, run_id, raw_input=raw_input, breakdown=breakdown, impact_map=impact,
            cheap_results=ctx["cheap_results"], rerank_result=ctx["rerank_result"],
            corrobs=ctx["corrobs"],
            trace=[f"assessment={breakdown.assessment}",
                   f"evidence_items={len(impact.evidence_items)}",
                   f"duration_ms={summary_meta['duration_ms']}"],
            errors=errors, summary_meta=summary_meta)
    return impact, ctx


def analyze(raw_input: str, source_kind: str, registry: ProviderRegistry, *, llm,
            profile=None, corroboration_config=None, artifact_root=None,
            requirement_id=None, now=None, short_hash: str = "run"):
    """端到端便捷入口:跑 02 breakdown -> 编排 -> 落盘。返回 ImpactMap(整合 smoke / Plan 10 用)。"""
    from contextos.requirement import breakdown as run_breakdown
    bd = run_breakdown(raw_input, source_kind, llm=llm, profile=profile,
                       requirement_id=requirement_id)
    raw = raw_input if isinstance(raw_input, str) else str(raw_input)
    impact, _ctx = run_and_persist(bd, registry, raw_input=raw,
                                   corroboration_config=corroboration_config,
                                   artifact_root=artifact_root, now=now, short_hash=short_hash)
    return impact
