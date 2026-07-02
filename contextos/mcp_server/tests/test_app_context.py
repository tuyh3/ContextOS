"""AppContext lazy 共享资源契约测试(Plan 10 Task 2)。

设计思路:
- AppContext 持有进程级重资源(llm/engine/searcher/rag_provider/oracle_querier),
  每个资源 lazy + 缓存,跨请求复用,JDT 冷启不在 __init__ 付。
- 本测试只验证三件低成本可判的契约,不起真 JDT / 真 Oracle:
  1. llm_override 注入生效 + 同一实例缓存(lazy 只构造一次)。
  2. engine lazy 缓存(lineage + config 共用同一 engine,一处构造)。
  3. oracle_querier() 在凭据缺失/未配时降级返 None,不抛(离线安全)。

评分标准(自动):三条 assert 全绿 = 契约满足;pyright 0。
人工 gold:Task 11 smoke 用真构建态(仓根 database/)验 searcher/rag_provider 真起。
"""
from __future__ import annotations

from pathlib import Path

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


def test_llm_override_used_and_cached(make_profile) -> None:
    ctx = AppContext.from_profile(make_profile(), llm_override=_FakeLLM())
    assert ctx.llm is ctx.llm                 # 同一实例(lazy 缓存,只构造一次)
    assert isinstance(ctx.llm, _FakeLLM)      # override 生效


def test_engine_lazy_and_shared(make_profile, tmp_path: Path) -> None:
    ctx = AppContext.from_profile(
        make_profile(data_dir=tmp_path), llm_override=_FakeLLM()
    )
    assert ctx.engine is ctx.engine           # 一个 engine,lineage + config 共用


def test_oracle_querier_degrades_when_unconfigured(make_profile, monkeypatch) -> None:
    # 删除可能存在的 .env 注入凭据,保证离线判定确定(无凭据 -> connect 即 raise)。
    monkeypatch.delenv("ORACLE_TEST_DB1_USER", raising=False)
    monkeypatch.delenv("ORACLE_TEST_DB1_PASSWORD", raising=False)
    ctx = AppContext.from_profile(make_profile(), llm_override=_FakeLLM())
    assert ctx.oracle_querier() is None       # 离线/未配 -> None,不抛


# --- 2026-06-10 Plan 04b T14: searcher 切投影(零 JDT), JDT 构造保留为 jdt_adapter ---
# searcher=ProjectionSearcher 的契约测试在 test_projection_tools.py(查询路径不构造 JDT);
# 这里守 jdt_adapter(仅 build 期消费)的 fail-safe:start 失败 catch 不传播。
# 原 prewarm_searcher 已删(投影查表秒回, 无 JDT 可预热), 两个 prewarm 测试随之移除。

def test_jdt_adapter_start_failure_is_fail_safe(make_profile, monkeypatch) -> None:
    """JDT start 失败(lombok 路径/环境/超时)时 jdt_adapter 内部 catch 不传播,返回
    unstarted adapter -> build 期消费方(init 抽样对照)用时 "Adapter not started" 自行
    降级不崩。守护 Plan 10 smoke 抓的真 bug 修复(不可回退成 start 异常直接传播)。"""
    import contextos.mcp_server.app_context as appctx_mod

    class _FailingAdapter:
        def __init__(self, **_kw: object) -> None: ...
        def start(self) -> None:
            raise RuntimeError("Provided lombok_path does not exist")   # 模拟环境失败
        def request_workspace_symbol(self, _query: str) -> list[object]:
            raise RuntimeError("Adapter not started")

    monkeypatch.setattr(appctx_mod, "JdtlsAdapter", _FailingAdapter)
    ctx = AppContext.from_profile(make_profile(), llm_override=_FakeLLM())
    assert ctx.jdt_adapter is not None   # start 失败被 catch,不传播 -> 返回 adapter(不抛)
