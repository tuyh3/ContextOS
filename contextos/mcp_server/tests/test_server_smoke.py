"""Task 3 端到端最小闭环 smoke(Plan 10 §4.2 高风险点先打通)。

测试思路:
  验证 MCP 管道 + 08 analyze() 串通 —— in-memory `Client(server)` 调
  build_impact_map,看 02 breakdown -> registry -> 编排 -> ImpactMap 全链路跑通,
  **不**验证检索准度(那是 Task 11 人工 smoke 的事)。

夹具 fake_app_ctx(见 conftest.fake_app_ctx):
  - llm = FakeLLM(4 响应队列 scope/extract/classify/translate)让 02 产 ok / 让
    prefilter 直接 rejected(不耗响应)。
  - searcher = _FakeSearcher(request_workspace_symbol 恒返 []) -> code_search miss。
  - rag_provider = _FakeRag(search 恒返 miss) -> RAG 桥 miss。
  - engine = 真内存 SQLite + 05/06 空表(create_all)-> lineage/config 桥读空表 miss,
    不崩(对齐 05/06 离线范式;比让 fake 抛异常靠 try/except 兜底干净)。
  目的:cheap 四桥全 miss + rerank 因空候选池 miss(不调 LLM),analyze 仍产合法
  ImpactMap(version 顶层字段在,evidence_items=[])。

评分标准:
  - 正常需求("新增动态计费批量操作"):res.data 是 dict,含顶层 version + evidence_items
    键(01 schema),管道不崩。
  - rejected 需求("9.9-9.11=?"):02 prefilter 早退 -> 所有桥见 rejected -> 全 miss ->
    evidence_items == [],MCP 层不抛(analyze fail-safe 兜底,Task 9 才加 ToolError 包装)。
"""
from __future__ import annotations

import pytest
from fastmcp import Client

from contextos.mcp_server.server import build_server


@pytest.fixture
def server(fake_app_ctx):
    return build_server(fake_app_ctx)


async def test_build_impact_map_tool_returns_impact_dict(server):
    async with Client(server) as client:
        res = await client.call_tool(
            "build_impact_map", {"requirement": "新增动态计费批量操作,完成后发短信"}
        )
        assert res.data["impact_map"]["version"]              # 01 schema 顶层字段(envelope 套了一层)
        assert "evidence_items" in res.data["impact_map"]
        assert set(res.data) == {"response_schema_version", "summary", "impact_map"}


async def test_rejected_requirement_still_returns_map(server):
    async with Client(server) as client:
        res = await client.call_tool("build_impact_map", {"requirement": "9.9-9.11=?"})
        assert res.data["impact_map"]["evidence_items"] == []   # 02 guard rejected -> 空 evidence, 不崩
