"""人工 smoke harness(Plan 10 Task 11)—— gold-standard 人工核验,不进 pytest。

为什么不进 pytest:这个 smoke 需要一个**真构建态**(05 lineage + 06 config 已 build 进
仓根 database/contextos.db,03 物化语料在 materialized/)、真 JDT LS workspace、真
DeepSeek key,跑一条真需求看三维 Impact Map 是否非空、证据 tool 是否返真表/配置。这些
都不是离线单测能覆盖的(单测用中性 fixture + fake 资源,守 tests-pyright-0 + 不依赖真
Oracle/JDT/网络)。所以它是一个独立可执行脚本,由人手在配好环境后跑一次,肉眼核结果。

怎么跑
------
  export CONTEXTOS_PROFILE=/path/to/ContextOS/config/profile.toml   # 指向真构建态的 profile
  export DEEPSEEK_API_KEY=...                                    # 02 需求拆解 + 07 重排要用
  uv run python -m contextos.mcp_server.smoke.smoke_real

profile 不在命令行传,走 load_profile 的标准搜索优先级($CONTEXTOS_PROFILE -> ./profile.toml
-> ./config/profile.toml -> ./data/profile.toml -> ~/.config/contextos/ -> ~/contextos-fpa/,
末两级是历史兼容 fallback)。找不到 profile 直接退出并提示(不静默假装成功)。

做什么
------
1. load_profile -> AppContext.from_profile -> build_server(与 MCP host 走的完全同一条路)。
2. 用 in-memory Client(server) 调 build_impact_map 一条真需求("新增动态计费批量操作"),
   打印顶层三维摘要(dimension_status / evidence_items 计数 / 前几条证据)。
3. 抽查 6 个 tool:lookup_table / lookup_config / search_code / trace_method_dataflow /
   health_check / profile_info,各打印一小段返回供人眼核(真表/配置/体检/脱敏)。

判据(人工)
----------
- build_impact_map 出非空三维 evidence(至少 method 维有候选);02 guard 不误拒真需求。
- health_check 各项 ready/cold/offline 合理(JDT 没被冷启拖死、Oracle 离线时标 offline)。
- profile_info 只回实例名 / corpus 名 / 路径 / 缺失必填项,**不**含任何凭据值。
- 证据 tool 返真表/配置(注意观察 02 术语 vs 真 CB_* schema 名 gap,Plan 09 解,不阻塞)。

抽查用的表名/配置 key 是占位:真构建态里未必恰好有同名实体,返空也正常(脚本只验证
管道串通 + 不崩,真业务命中由人看 build_impact_map 的三维结果判)。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from fastmcp import Client

from contextos.mcp_server.app_context import AppContext
from contextos.mcp_server.server import build_server
from contextos.profile.loader import ProfileNotFound, load_profile

# 一条真需求(与 Plan 08 真 DeepSeek smoke 同源:动态计费批量操作)。
_REQUIREMENT = "新增动态计费批量操作"

# 抽查证据 tool 的占位入参(真构建态里未必同名,返空也算通过 —— 只验管道不崩)。
_SAMPLE_TABLE = "CB_CUSTOMER"
_SAMPLE_CONFIG_KEY = "feature.flag.example"
_SAMPLE_CODE_QUERY = "DynamicCharging"
_SAMPLE_SOURCE_PATH = "CustomerService.java"


def _hr(title: str) -> None:
    """打印一条分节标题(纯 ASCII 分隔,便于人眼扫描)。"""
    print("\n" + "=" * 8 + " " + title + " " + "=" * 8)


def _dump(label: str, value: Any, *, limit: int = 1200) -> None:
    """把一段返回值美化打印(中文可读),超长截断,供人核。"""
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) > limit:
        text = text[:limit] + f"\n... (truncated, total {len(text)} chars)"
    print(f"[{label}]\n{text}")


async def _wrapped_tools(client: Client[Any]) -> frozenset[str]:
    """从 list_tools 的 outputSchema 收集带 x-fastmcp-wrap-result 标记的 tool 名。

    FastMCP 对非 object 返回型(如 `list[dict]`)会把 structured content 包成
    {"result": ...} 并在 outputSchema 打该标记;_call 据此解包。
    """
    tools = await client.list_tools()
    return frozenset(
        t.name for t in tools
        if (t.outputSchema or {}).get("x-fastmcp-wrap-result"))


async def _call(client: Client[Any], name: str, args: dict[str, Any],
                *, wrapped: frozenset[str] = frozenset()) -> Any:
    """调一个 tool,返回 wire 上的结构化数据;失败不抛,打印错误后返 None。

    读 structured_content 而非 res.data:res.data 是 fastmcp 客户端按 output schema
    的再反序列化,对 `list[dict[str, Any]]` 返回型(元素 Any 建不出精确 schema)会把
    每个元素吞成无字段占位 Root()(2026-06-12 实测 search_code 17 条全打成 "Root()");
    structured_content 才是 MCP host 真看到的 payload。name 在 wrapped 集里时解掉
    FastMCP 的 {"result": ...} 包装(对齐 test_evidence_tools._list_payload 既有定式)。

    raise_on_error=False:让 middleware/ToolError 路径也能被人看到(而非脚本中途崩掉),
    一个 tool 抽查失败不该阻断其余抽查。
    """
    try:
        res = await client.call_tool(name, args, raise_on_error=False)
        if getattr(res, "is_error", False):
            print(f"[{name}] tool returned isError: {getattr(res, 'content', None)}")
            return None
        sc = res.structured_content
        if sc is None:  # 无结构化输出的 tool(当前没有):退回 .data 兜底
            return res.data
        if name in wrapped and isinstance(sc, dict):
            return sc.get("result")
        return sc
    except Exception as exc:  # 网络/资源类异常:打印后继续抽查其余 tool
        print(f"[{name}] call raised: {exc!r}")
        return None


def _summarize_impact_map(impact: Any) -> None:
    """打印 build_impact_map 的三维摘要(顶层 01 schema 字段)。"""
    if not isinstance(impact, dict):
        print(f"build_impact_map 返回非 dict:{type(impact)!r} -> {impact!r}")
        return
    evidence = impact.get("evidence_items") or []
    print(f"requirement_id      : {impact.get('requirement_id')}")
    print(f"requirement_summary : {impact.get('requirement_summary')}")
    print(f"dimension_status    : {impact.get('dimension_status')}")
    print(f"known_limitations   : {impact.get('known_limitations')}")
    print(f"matched_capabilities: {impact.get('matched_business_capabilities')}")
    print(f"evidence_items 计数  : {len(evidence)}")
    for i, item in enumerate(evidence[:5]):
        if isinstance(item, dict):
            print(
                f"  evidence[{i}] kind={item.get('kind')} "
                f"target={item.get('target')} confidence={item.get('confidence')}"
            )


async def _run(app_ctx: AppContext) -> None:
    """起 in-memory Client,跑 build_impact_map + 6 个抽查 tool。"""
    server = build_server(app_ctx)
    async with Client(server) as client:
        wrapped = await _wrapped_tools(client)

        _hr("build_impact_map(真需求三维)")
        print(f"requirement: {_REQUIREMENT}")
        impact = await _call(
            client, "build_impact_map", {"requirement": _REQUIREMENT}, wrapped=wrapped
        )
        _summarize_impact_map(impact)

        _hr("lookup_table(占位表名,返空也正常)")
        _dump(
            "lookup_table",
            await _call(client, "lookup_table", {"table": _SAMPLE_TABLE},
                        wrapped=wrapped),
        )

        _hr("lookup_config(占位 key,返空也正常)")
        _dump(
            "lookup_config",
            await _call(client, "lookup_config", {"config_key": _SAMPLE_CONFIG_KEY},
                        wrapped=wrapped),
        )

        _hr("search_code(04b code_* 投影查表,零 JDT)")
        _dump(
            "search_code",
            await _call(client, "search_code", {"query": _SAMPLE_CODE_QUERY},
                        wrapped=wrapped),
        )

        _hr("trace_method_dataflow(占位源文件,返空也正常)")
        _dump(
            "trace_method_dataflow",
            await _call(
                client, "trace_method_dataflow", {"source_path": _SAMPLE_SOURCE_PATH},
                wrapped=wrapped
            ),
        )

        _hr("health_check(各子系统状态)")
        _dump("health_check", await _call(client, "health_check", {}, wrapped=wrapped))

        _hr("profile_info(脱敏:不应含任何凭据值)")
        _dump("profile_info", await _call(client, "profile_info", {}, wrapped=wrapped))


def main() -> int:
    """加载真 profile -> build AppContext -> 跑 smoke。返回进程退出码。"""
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print(
            "提示:未设 DEEPSEEK_API_KEY。02 需求拆解 / 07 重排会因缺凭据降级或报错,"
            "build_impact_map 三维可能为空。设好再跑可看完整结果。",
            file=sys.stderr,
        )
    try:
        profile = load_profile(None)
    except ProfileNotFound as exc:
        print(
            "未找到 profile.toml。请 export CONTEXTOS_PROFILE=<指向真构建态的 profile.toml> "
            f"后重试。\n详情:{exc}",
            file=sys.stderr,
        )
        return 2

    app_ctx = AppContext.from_profile(profile)
    asyncio.run(_run(app_ctx))
    print("\nsmoke 跑完。请人工核:三维非空 / health 合理 / profile_info 不泄凭据 / 证据 tool 返真值。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
