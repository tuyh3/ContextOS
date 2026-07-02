"""build_impact_map MCP tool 接线测试(envelope + instructions + CLI 不回归)。

设计思路: build_server(fake_app_ctx) 起真 FastMCP, in-memory Client 调 tool 验 envelope;
另直接调 build_impact_map_impl 验 CLI 路径仍是完整 01 dict(无 summary 键)。
评分标准: tool 出 envelope 三键; instructions 含 full=true/summary token; impl 路径不带 envelope。
脚本逻辑: 复用 conftest 的 fake_app_ctx; FastMCP Client 异步协议(asyncio_mode=auto)。
"""
from __future__ import annotations

from fastmcp import Client

from contextos.mcp_server.server import build_server
from contextos.mcp_server.tools.impact_map import build_impact_map_impl


def test_instructions_present_with_tokens(fake_app_ctx):
    server = build_server(fake_app_ctx)
    instr = server.instructions or ""
    assert "full=true" in instr
    assert "summary" in instr


def test_cli_path_impl_returns_plain_01_no_envelope(fake_app_ctx):
    # CLI query 走 build_impact_map_impl: 仍是完整 01 dict, 无 envelope 键(不回归)
    result = build_impact_map_impl(fake_app_ctx, requirement="新增动态计费批量操作")
    assert "response_schema_version" not in result
    assert "summary" not in result
    assert "evidence_items" in result   # 01 schema 顶层字段


async def test_tool_returns_envelope(fake_app_ctx):
    async with Client(build_server(fake_app_ctx)) as client:
        res = await client.call_tool("build_impact_map",
                                     {"requirement": "新增动态计费批量操作"})
        data = res.data
        assert set(data) == {"response_schema_version", "summary", "impact_map"}
