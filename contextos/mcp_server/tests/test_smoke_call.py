"""smoke_real._call 的离线回归测试(fastmcp 客户端 .data 反序列化丢字段 bug)。

设计思路
--------
2026-06-12 真跑 smoke_real 发现:search_code(返回注解 `list[dict[str, Any]]`)段
打出 17 条裸字符串 "Root()" 而非真实 FQN。根因不在 server / 查询层:FastMCP 对元素
Any 的 list 返回型 tool 生成 {"result": {"items": {"type": "object"}}} wrapper schema
(x-fastmcp-wrap-result),fastmcp Client 的 res.data 按该 schema 把每个 dict 反序列化
成无字段占位模型 Root();wire 上的 structured_content / text 始终完好(真 MCP host
不受影响)。修法 = _call 改读 structured_content 并按 x-fastmcp-wrap-result 标记解包
(对齐 test_evidence_tools._list_payload 既有定式),不再依赖有损的 res.data。

评分标准
--------
- list[dict] 返回型 tool:_call 返回元素是真 dict 且字段值无损(target/kind/score)。
- dict 返回型 tool:行为不回归,字段原样可读。
- tool 报错(is_error)路径:_call 返 None 不抛(smoke 抽查互不阻断语义不变)。
- _wrapped_tools:只把带 x-fastmcp-wrap-result 的 tool 列入解包集(dict 返回型不解包)。

自动脚本逻辑
------------
内联 3-tool FastMCP fixture server(中性合成值,不掺真客户标识),in-memory Client
走完整协议;_wrapped_tools 从 list_tools 的 outputSchema 读 x-fastmcp-wrap-result
标记,_call 据此解包。断言只绑 "_call 返回真数据" 这一契约,不绑 fastmcp 内部实现
(将来库修复 .data 行为,本测试依然成立)。
"""
from __future__ import annotations

from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from contextos.mcp_server.smoke.smoke_real import _call, _wrapped_tools


def _fixture_server() -> FastMCP:
    """3 个形态代表:list[dict](同 search_code)/ dict(同 lookup_table)/ 报错。"""
    mcp = FastMCP("smoke-call-fixture")

    @mcp.tool()
    def list_rows() -> list[dict[str, Any]]:
        return [
            {"target": "com.acme.OrderService", "kind": "CLASS", "score": 0.6},
            {"target": "com.acme.BillingService", "kind": "CLASS", "score": 0.6},
        ]

    @mcp.tool()
    def dict_info() -> dict[str, Any]:
        return {"table": "ORDERS", "edges_out": 1}

    @mcp.tool()
    def boom() -> dict[str, Any]:
        raise ToolError("boom")

    return mcp


async def test_wrapped_tools_marks_only_list_tool():
    async with Client(_fixture_server()) as client:
        wrapped = await _wrapped_tools(client)
        assert "list_rows" in wrapped       # 非 object 返回型被 FastMCP 包 result
        assert "dict_info" not in wrapped   # dict 返回型不包、不解


async def test_call_list_tool_fields_intact():
    async with Client(_fixture_server()) as client:
        wrapped = await _wrapped_tools(client)
        rows = await _call(client, "list_rows", {}, wrapped=wrapped)
        assert rows == [
            {"target": "com.acme.OrderService", "kind": "CLASS", "score": 0.6},
            {"target": "com.acme.BillingService", "kind": "CLASS", "score": 0.6},
        ]


async def test_call_dict_tool_passthrough():
    async with Client(_fixture_server()) as client:
        wrapped = await _wrapped_tools(client)
        info = await _call(client, "dict_info", {}, wrapped=wrapped)
        assert info == {"table": "ORDERS", "edges_out": 1}


async def test_call_error_returns_none():
    async with Client(_fixture_server()) as client:
        out = await _call(client, "boom", {}, wrapped=frozenset())
        assert out is None
