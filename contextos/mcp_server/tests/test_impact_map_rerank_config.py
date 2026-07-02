"""Plan 07 旋钮接 profile (2026-06-09): build_impact_map 把 profile.llm_rerank 映射成 RerankConfig
并喂给 rerank()(此前 rerank_config=None 用写死默认)。

设计思路: helper rerank_config_from_profile(profile) 做纯映射(可单测); build_impact_map_impl 调它
并把结果传 build_default_registry(rerank_config=...)。验证两层: (1) 映射正确; (2) 真接到 registry。

评分标准(自动): helper 默认/override 映射正确 + build_impact_map_impl 把 profile 派生的 config 传给
build_default_registry(monkeypatch 捕获)。
"""
from __future__ import annotations

from typing import Any

from contextos.mcp_server.tools.impact_map import (
    build_impact_map_impl,
    rerank_config_from_profile,
)
from contextos.rerank.schema import RerankConfig


def test_rerank_config_from_profile_defaults(make_profile):
    rc = rerank_config_from_profile(make_profile())
    assert isinstance(rc, RerankConfig)
    assert rc.batch_size == 8 and rc.max_concurrency == 6
    assert (rc.method_cap, rc.sql_cap, rc.config_cap) == (30, 30, 20)


def test_rerank_config_from_profile_honors_override(make_profile):
    p = make_profile()
    p2 = p.model_copy(update={"llm_rerank": p.llm_rerank.model_copy(
        update={"batch_size": 4, "max_concurrency": 2, "method_cap": 12})})
    rc = rerank_config_from_profile(p2)
    assert rc.batch_size == 4 and rc.max_concurrency == 2 and rc.method_cap == 12


def test_build_impact_map_passes_profile_rerank_config(make_profile, monkeypatch):
    """端到端接线: build_impact_map_impl 必须把 profile.llm_rerank 派生的 RerankConfig 传给
    build_default_registry(否则 rerank 仍用写死默认, profile 旋钮白配)。"""
    import contextos.mcp_server.tools.impact_map as im

    captured: dict[str, Any] = {}

    def _fake_registry(**kw: Any) -> object:
        captured["rerank_config"] = kw.get("rerank_config")
        return object()

    class _FakeImpact:
        def model_dump(self, mode: str = "json") -> dict:
            return {}

    monkeypatch.setattr(im, "build_default_registry", _fake_registry)
    monkeypatch.setattr(im, "analyze", lambda *a, **k: _FakeImpact())

    p = make_profile()
    p2 = p.model_copy(update={"llm_rerank": p.llm_rerank.model_copy(
        update={"max_concurrency": 2, "batch_size": 4})})

    class _Ctx:
        profile = p2
        llm = object()
        searcher = object()
        rag_provider = object()
        engine = object()

    build_impact_map_impl(_Ctx(), requirement="x")  # type: ignore[arg-type]
    rc = captured["rerank_config"]
    assert isinstance(rc, RerankConfig)
    assert rc.max_concurrency == 2 and rc.batch_size == 4   # 用了 profile 值, 不是写死默认 6/8
