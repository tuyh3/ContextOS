"""build_impact_map —— Plan 10 主入口 tool 的 core 实现(08 analyze 薄包装)。

设计(spec §4.2 资源生命周期):
  build_default_registry 的 `shared` dict 是 per-registry 可变态,注释钉死"并发请求
  须每请求 build 一个 registry"。故这里**每次调用新建 registry**(shared 隔离),
  重资源(searcher/engine/rag_provider/llm)仍由进程级 AppContext 共享。

  reg = build_default_registry(searcher, rag_provider, lineage_engine=engine,
                               config_engine=engine, llm)  # 05/06 表同库 -> 同 engine
  impact = analyze(requirement, adapter_kind, reg, llm=app_ctx.llm, profile=...)
  return impact.model_dump(mode="json")   # 01 schema dict(顶层 version / evidence_items / ...)

异常处理:输入面拦截在 middleware(Task 9 已实装);本入口不包 ToolError,靠 analyze
自身的 fail-safe 兜底(02 guard rejected -> 空 evidence;各桥失败 -> miss,不抛)。
top_n / corpora 入签名保 API 稳定,但 v1 未下沉到 analyze(corpora 由 middleware
校验白名单;top_n 截断留后续),按契约声明意图、当前不改 analyze 行为。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from contextos.orchestrator.pipeline import analyze
from contextos.orchestrator.registry import build_default_registry
from contextos.rerank.schema import RerankConfig
from contextos.mcp_server.tools.impact_map_view import (
    compact_evidence,
    summarize,
    verify_no_sensitive,
)

if TYPE_CHECKING:
    from contextos.mcp_server.app_context import AppContext
    from contextos.profile.schema import Profile


def rerank_config_from_profile(profile: Profile) -> RerankConfig:
    """profile.[llm_rerank] 运行旋钮 -> rerank() 消费的 RerankConfig(纯映射, 可单测)。

    profile 层不 import rerank(避免基础层依赖功能模块), 故映射在本集成层做; rag_summary_max_chars
    不暴露到 profile, 用 RerankConfig 内部默认。
    """
    r = profile.llm_rerank
    return RerankConfig(
        batch_size=r.batch_size,
        max_concurrency=r.max_concurrency,
        method_cap=r.method_cap,
        sql_cap=r.sql_cap,
        config_cap=r.config_cap,
    )


def build_impact_map_impl(
    app_ctx: AppContext,
    *,
    requirement: str,
    adapter_kind: str = "text",
    top_n: int = 50,
    corpora: list[str] | None = None,
) -> dict[str, Any]:
    """给需求文本,返回三维 Impact Map(01 schema dict)。每请求新建 registry。"""
    reg = build_default_registry(
        searcher=app_ctx.searcher,
        rag_provider=app_ctx.rag_provider,
        lineage_engine=app_ctx.engine,
        config_engine=app_ctx.engine,
        llm=app_ctx.llm,
        rerank_config=rerank_config_from_profile(app_ctx.profile),
    )
    impact = analyze(
        requirement,
        adapter_kind,
        reg,
        llm=app_ctx.llm,
        profile=app_ctx.profile,
        corroboration_config=app_ctx.profile.corroboration,   # 让 pipeline(folding/corroboration/dimension_quality)用 profile N
    )
    return impact.model_dump(mode="json")


MAX_COMPACT_TOP_N = 200
RESPONSE_SCHEMA_VERSION = "build_impact_map/v1"


def _clamp_top_n(top_n: int) -> int:
    try:
        n = int(top_n)
    except (TypeError, ValueError):
        return 50
    return max(1, min(n, MAX_COMPACT_TOP_N))


def build_impact_map_mcp_response(
    app_ctx: AppContext,
    *,
    requirement: str,
    adapter_kind: str = "text",
    top_n: int = 50,
    corpora: list[str] | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """MCP 出口包装: 完整 01 dict -> response envelope(summary + 紧凑/全量 impact_map)。

    build_impact_map_impl 保持返回完整 01 dict 不变(CLI 用); 本函数只在 MCP 出口套壳。
    """
    impact = build_impact_map_impl(
        app_ctx, requirement=requirement, adapter_kind=adapter_kind,
        top_n=top_n, corpora=corpora,
    )
    n = app_ctx.profile.corroboration.consensus_min_bridges
    capped = _clamp_top_n(top_n)
    full_evidence = impact.get("evidence_items") or []

    if full:
        view, empty_core = list(full_evidence), False
    else:
        view, empty_core = compact_evidence(
            full_evidence, consensus_min_bridges=n, top_n=capped)

    # summary 必须在替换 evidence_items 之前算(基于完整集)
    summary = summarize(impact, returned=len(view), full=full,
                        empty_core_fallback=empty_core)

    impact_view = dict(impact)
    impact_view["evidence_items"] = view
    envelope = {
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "summary": summary,
        "impact_map": impact_view,
    }
    verify_no_sensitive(envelope)   # 出口脱敏 fail-closed(default/full 两路径都过)
    return envelope
