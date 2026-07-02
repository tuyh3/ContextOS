"""AppContext.oracle_router() 懒建 DbRouter + 缓存测试(Block 1b Task 14)。

设计思路
--------
oracle_router() 应:
1. 返回 DbRouter 实例(非 None,即便 Oracle 未连 -- router 内部降级,不是 None)。
2. 同一 AppContext 实例多次调用返回同一对象(lazy + 缓存)。
3. engine 构建异常时安全降级返 None(不抛)。

评分标准
--------
- oracle_router() 两次调用返回同一实例(r1 is r2)。
- isinstance(router, DbRouter)。
- engine 构建失败 -> oracle_router() 降级 None 不抛。
"""
from __future__ import annotations

from typing import Any

import pytest

from contextos.llm.base import LLMProvider
from contextos.mcp_server.app_context import AppContext


class _FakeLLM(LLMProvider):
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        return "{}"


def test_oracle_router_lazy_and_cached(make_profile) -> None:
    """oracle_router() 返回缓存的同一 DbRouter 实例(lazy + 进程级共享)。"""
    prof = make_profile()
    ctx = AppContext.from_profile(prof, llm_override=_FakeLLM())
    r1 = ctx.oracle_router()
    r2 = ctx.oracle_router()
    assert r1 is r2                         # 缓存同一实例

    from contextos.lineage.db_router import DbRouter
    assert isinstance(r1, DbRouter)


def test_oracle_router_degrades_when_dbrouter_raises(make_profile, monkeypatch) -> None:
    """DbRouter 构造抛异常时 oracle_router() 降级返 None,不抛。"""
    # patch app_context 内部的 DbRouter 导入(oracle_router 用 lazy import)
    import contextos.lineage.db_router as dr_mod

    class _BoomRouter:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            raise RuntimeError("simulated router build failure")

    monkeypatch.setattr(dr_mod, "DbRouter", _BoomRouter)
    prof = make_profile()
    ctx = AppContext.from_profile(prof, llm_override=_FakeLLM())
    r = ctx.oracle_router()
    assert r is None                         # 构造失败 -> 降级 None 不抛
