"""FastMCP server 骨架(Plan 10 §2 / §4)。

build_server(app_ctx) -> FastMCP:建 FastMCP 实例,注册对外 tool。
当前注册:主入口 build_impact_map(Task 3 端到端闭环)+ 16 证据 tool(Task 7 建 13,
Plan 04b T14 扩 15,覆盖基座 ① T7 加 search_source 扩 16,register_evidence_tools)+
3 元工具(Task 8,register_meta_tools:
health_check / profile_info / incremental_rebuild)+ 1 运维回写工具(ops-B,register_ops_tools:
record_confirmed_case,human-gated 确诊案例回写)+ input validation middleware(Task 9,
InputValidationMiddleware:tool call 前粗粒度硬隔离 host 输入,红线 #8/#9)。
共 21 tool(数目 SSOT = 10 §3)。

资源模型(spec §4.2):app_ctx 是进程级共享重资源(lazy);每个 tool 闭包捕获 app_ctx,
build_impact_map 内部每请求新建 registry(shared 隔离)。tool 函数是 sync(FastMCP
支持 sync tool);core 全 sync,MCP 层异步只在 transport / Client 测试侧。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP

from contextos.mcp_server.middleware import InputValidationMiddleware
from contextos.mcp_server.tools.evidence import register_evidence_tools
from contextos.mcp_server.tools.impact_map import build_impact_map_mcp_response
from contextos.mcp_server.tools.meta import register_meta_tools
from contextos.mcp_server.tools.ops import register_ops_tools
from contextos.ops.paths import ensure_confirmed_cases_dir

if TYPE_CHECKING:
    from contextos.mcp_server.app_context import AppContext

log = logging.getLogger(__name__)


_SERVER_INSTRUCTIONS = (
    "ContextOS = 需求影响定位器。build_impact_map 给需求 -> 三维候选(方法/SQL 表/配置)"
    "+ 证据 + 置信度。\n"
    "读法: 先读 summary(统计 + dimension_quality + recommended_use), 再看 evidence_items。\n"
    "四桥各用什么: 代码桥=JDT LS workspaceSymbol; SQL 桥=sqlglot/sql-recover 血缘; "
    "配置桥=绑定解析, 找不到退化 ripgrep 全文搜(grep 命中非真绑定); RAG=ripgrep sparse 检索(非 embedding)。\n"
    "候选是'猜了等下游验', 不是判决。两轴: dimension_status=覆盖状态 / "
    "dimension_quality=证据强弱(fallback_only 表 grep 兜底, 谨慎采信)。\n"
    "默认只返回多桥共识强核; 要全部候选(含被折叠弱线索)传 full=true。"
)


def build_server(app_ctx: AppContext) -> FastMCP:
    """建 FastMCP 实例 + 注册 tool。app_ctx 由调用方(CLI serve-mcp / 测试)建好传入。"""
    mcp: FastMCP = FastMCP("contextos", instructions=_SERVER_INSTRUCTIONS)

    @mcp.tool()
    def build_impact_map(
        requirement: str,
        adapter_kind: str = "text",
        top_n: int = 50,
        corpora: list[str] | None = None,
        full: bool = False,
    ) -> dict[str, Any]:
        """给需求文本, 返回三维 Impact Map 的 response envelope。

        先读 summary(统计 + dimension_quality + recommended_use), 再看 impact_map.evidence_items。
        默认只返回多桥共识强核(紧凑); full=true 取全部 evidence_items(含被折叠/单桥弱线索)。
        """
        return build_impact_map_mcp_response(
            app_ctx,
            requirement=requirement,
            adapter_kind=adapter_kind,
            top_n=top_n,
            corpora=corpora,
            full=full,
        )

    # 16 证据 tool(Task 7 + 04b T14 + 覆盖基座 ① T7):薄包装 core *tools.py / dataflow,异常转 ToolError + 脱敏。
    register_evidence_tools(mcp, app_ctx)

    # 3 元工具(Task 8):health_check / profile_info(脱敏)/ incremental_rebuild
    # (code scope 04b T14 实装,其余维度占位)。
    register_meta_tools(mcp, app_ctx)

    register_ops_tools(mcp, app_ctx)
    # spec Appendix C MUST: confirmed-cases 空目录也创建, strict scope 不回退全量。
    # 起不来不挡 server 启动(查询本身不依赖它存在), 故 try/except 兜底。
    try:
        ensure_confirmed_cases_dir(app_ctx.profile)
    except Exception as exc:  # noqa: BLE001
        log.warning("ensure_confirmed_cases_dir failed (server continues): %s", exc)

    # input validation middleware(Task 9 + 04b T14):tool call 前粗粒度硬隔离(红线 #8/#9)——
    # ad-hoc corpus / 连接注入 / 非只读 SQL / FQN 形态 四类统一拦截,挡在 tool body 之前。
    mcp.add_middleware(InputValidationMiddleware(app_ctx))

    return mcp
