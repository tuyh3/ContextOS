"""record_confirmed_case MCP tool 包装(组件 B, spec Appendix A)。

薄包装(对齐 meta.py 模式): impl 在 contextos.ops.recorder, 本层只注册成 @mcp.tool 并把
异常转 ToolError(不裸传 traceback 给不可信 host, 红线 #9)。human-gated: 只在专家确认某
假设为真根因后由 host 调(Phase 5)。actor_id 服务端注入(单机默认 local-user), 非 host 参数。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from contextos.ops.recorder import record_confirmed_case_impl

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_ops_tools(mcp: "FastMCP", app_ctx: Any) -> None:
    @mcp.tool()
    def record_confirmed_case(
        phenomenon_signature: str,
        search_terms: str,
        behavior_class: str,
        confirmed_root_cause: str,
        mechanism_tag: str,
        evidence_pointers: list[str],
        confirmed_by_role: str,
        source_type: str,
        decisive_data_note: str | None = None,
        source_ref: str | None = None,
        relation: str | None = None,
    ) -> dict[str, Any]:
        """[human-gated, Phase 5] 专家确认根因后回写确诊案例库。

        校验 + PII gate + 去重四分支 + 物化 markdown + 审计 sidecar + 同义池积累。
        confirmed_by_actor_id 服务端注入(非参数, 防伪造)。
        """
        try:
            return record_confirmed_case_impl(
                app_ctx,
                phenomenon_signature=phenomenon_signature,
                search_terms=search_terms,
                behavior_class=behavior_class,
                confirmed_root_cause=confirmed_root_cause,
                mechanism_tag=mechanism_tag,
                evidence_pointers=evidence_pointers,
                decisive_data_note=decisive_data_note,
                confirmed_by_role=confirmed_by_role,
                source_type=source_type,
                source_ref=source_ref,
                relation=relation,
            )
        except Exception as exc:
            raise ToolError(f"record_confirmed_case failed: {exc}") from exc
