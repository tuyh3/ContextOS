"""13 证据 tool 的 MCP 包装测试(Plan 10 Task 7)。

设计思路
--------
mcp_server/tools/evidence.py 的 register_evidence_tools(mcp, app_ctx) 把 13 个 core
证据 tool(lineage/tools.py + config_dim/tools.py + code_search/tools.py + recall/
rag_tool.py + lineage/dataflow.py)注册成 MCP tool。本层**只**做:解析参数 -> 取
app_ctx 资源 -> call core 函数 -> 异常转 ToolError -> 返 dict/list。**不写查询逻辑**
(查询逻辑在 WF2 的 core tools.py,已各自单测)。

本测试用 in-memory `Client(server)` 端到端验证每个 tool 经 MCP 协议可调 + 返合理形态:

1. 13 个 tool 各至少 1 条:call -> 返回 core 函数的 dict/list 结果(经 MCP 序列化)。
2. lineage 类走**离线分支**(querier=None):app_ctx.oracle_querier() 返 None,
   tool 仍返结构完整 dict(本地血缘 + note='oracle_offline')。
3. config 类的 patterns **从 profile.config.sensitive_key_patterns 取**(承接 WF2
   安全修复):本测试在 profile 里塞一个**非 floor** 的自定义敏感词(custom_secret_kw),
   config_items.description 里埋 custom_secret_kw=LEAKME,断言 tool 输出把它 mask ——
   证明 patterns 真从 profile 来(floor 不含此词,只靠 floor 漏不掉就说明是 profile 生效)。
4. 异常转 ToolError:lookup_table 传非法 identifier(分号)-> core 抛 ValueError ->
   MCP 层转 ToolError(经 Client 抛 Exception),不裸传 traceback。

评分标准
--------
- 13 tool 全可经 in-memory Client 调到,返回类型对(dict / list)。
- lineage 类离线 querier=None 不崩,返 note='oracle_offline'。
- config 类 patterns 来自 profile(自定义词 redact 生效)+ salt 来自 load_or_create_salt。
- search_code 走 app_ctx.searcher;rag_search 走 app_ctx.rag_provider。
- trace_method_dataflow 走 lineage.dataflow + app_ctx.engine。
- core ValueError -> ToolError(不裸抛)。

测试 fixture 用中性合成名(APP/ORDERS/ORDER_ITEMS/feature.flag.x),不掺真客户
schema/owner/表名(守 feedback_offline_test_neutral_fixtures)。salt/patterns 经
app_ctx 的 data_dir(load_or_create_salt)+ profile.config.sensitive_key_patterns。

自动脚本逻辑
------------
_EvidenceAppCtx = AppContext duck-typed 替身:一个真内存 SQLite engine 同时种入
05(lineage_edges/sql_templates/lineage_evidence)+ 06(config_items/config_entities/
config_bindings/rule_sets/rule_bindings)中性行;searcher=FakeSearcher、
rag_provider=FakeRagProvider、oracle_querier()->None(离线)。build_server(app_ctx)
+ register_evidence_tools(server, app_ctx) 后用 Client 逐 tool 调。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from contextos.config_dim import schema as config_schema
from contextos.lineage import store as lineage_store
from contextos.mcp_server.server import build_server

# profile.config.sensitive_key_patterns 自定义敏感词:刻意选 'adminpin' —— 它**不含**任何
# core floor 子串(password/passwd/secret/token/credential),故 floor-only 路径漏不掉它,
# 只有真从 profile 取 patterns 才会 mask。避免选含 'secret' 的词造成"floor 也命中"的伪绿
# (守 feedback_test_fixtures_match_real_contract:测试要能在契约被破坏时真失败)。
_CUSTOM_SECRET_KW = "adminpin"


def _list_payload(res: Any) -> list[Any]:
    """读 list 返回型 tool 的真数据。

    FastMCP 对 `-> list[dict[str, Any]]`(元素 Any)无法建精确元素 schema, res.data 把每个
    元素反序列化成不可下标的 Root() 占位;真 list 在 structured_content['result']。dict 返回型
    无此问题(res.data 直接是 dict)。故 list tool 统一从 structured_content 读。
    """
    return res.structured_content["result"]


# --------------------------------------------------------------------------- fixtures


def _seed_lineage(engine: Any) -> None:
    """中性合成血缘/SQL 模板(照搬 WF2 lineage/tests/test_tools._seed 形态)。"""
    lineage_store.create_all(engine)
    with engine.begin() as c:
        c.execute(lineage_store.lineage_edges.insert(), [
            {"edge_id": "e1", "src_owner": "APP", "src_table": "ORDERS",
             "dst_owner": "APP", "dst_table": "ORDER_ITEMS",
             "relation_type": "JOIN", "confidence": "high", "evidence_count": 2},
            {"edge_id": "e2", "src_owner": "APP", "src_table": "CUSTOMERS",
             "dst_owner": "APP", "dst_table": "ORDERS",
             "relation_type": "WRITE", "confidence": "medium", "evidence_count": 1},
        ])
        c.execute(lineage_store.sql_templates.insert(), [
            {"template_id": "t1", "source_file": "X.java", "container": "XSvc.run",
             "sql_text": "SELECT * FROM ORDERS WHERE ID=?",
             "recovery_mode": "sql_file", "confidence": "high"},
        ])
        # dataflow 路径 B: lineage_evidence 反查 evidence_ref 形如 source_path:line
        c.execute(lineage_store.lineage_evidence.insert(), [
            {"evidence_id": "ev1", "edge_id": "e1", "evidence_ref": "X.java:42",
             "snippet": "SELECT * FROM ORDERS o JOIN ORDER_ITEMS i"},
        ])


def _seed_config(engine: Any) -> None:
    """中性合成配置维(照搬 WF2 config_dim/tests/test_tools._seed 形态)。

    config_items.description 埋 custom_secret_kw=LEAKME 验证 profile patterns 生效。
    """
    config_schema.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(config_schema.config_sources.insert(), [
            {"source_id": "s1", "source_type": "file",
             "file_path": "application.properties", "db_name": "", "owner": "",
             "table_name": "", "module": "app", "description": "app config file"},
        ])
        c.execute(config_schema.config_entities.insert(), [
            {"entity_id": "en1", "source_id": "s1", "entity_key": "feature.flag.x",
             "entity_type": "file_key", "description": "feature flag x"},
        ])
        c.execute(config_schema.config_items.insert(), [
            {"item_id": "i1", "source_id": "s1", "entity_id": "en1",
             "snapshot_id": "snap1", "config_key": "feature.flag.x",
             "key_path": "feature.flag.x", "value_raw": "true", "value_type": "bool",
             "is_sensitive": 0,
             # 自定义敏感词(floor 不含)埋自由文本 -> 只有 profile patterns 生效才会 mask
             "description": f"toggle; set {_CUSTOM_SECRET_KW}=LEAKME9 to enable"},
        ])
        c.execute(config_schema.config_bindings.insert(), [
            {"binding_id": "b1", "entity_id": "en1", "bind_type": "java_class",
             "bind_target": "com.x.FeatureConfig", "bind_strategy": "exact_match",
             "bind_direction": "read", "confidence": "high",
             "evidence": "annotation@F.java:10"},
        ])
        c.execute(config_schema.rule_sets.insert(), [
            {"rule_set_id": "rs1", "name": "PricingRule", "source_id": "s1",
             "category": "pricing", "owner_domain": "billing", "status": "active",
             "description": "pricing rule set"},
        ])
        c.execute(config_schema.rule_bindings.insert(), [
            {"binding_id": "rb1", "rule_set_id": "rs1", "bind_type": "source_file",
             "bind_target": "PricingSvc.java", "bind_role": "subject",
             "evidence": "table_to_code"},
        ])


class _FakeSearcher:
    """SymbolSearcher 协议替身:按 query 返 canned 符号。"""

    def __init__(self, table: dict[str, list[dict[str, Any]]]) -> None:
        self.table = table

    def request_workspace_symbol(self, query: str) -> list[dict[str, Any]]:
        return list(self.table.get(query, []))


class _FakeRag:
    """RagProvider 替身:search 返 canned ProviderResult。"""

    def search(self, query: dict) -> Any:
        from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult

        return ProviderResult(
            worker_name="rag",
            score=0.91,
            candidates=[ProviderCandidate(
                target="app/charge.md", kind="BUSINESS_DOC",
                signals={"rerank_score": 0.91, "snippet": "feature flag config"})],
        )


def _sym(name: str, kind: int, rel: str, container: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": name, "kind": kind,
        "location": {"relativePath": rel, "uri": f"file://{rel}",
                     "range": {"start": {"line": 1, "character": 0},
                               "end": {"line": 9, "character": 0}}},
    }
    if container is not None:
        d["containerName"] = container
    return d


class _EvidenceAppCtx:
    """AppContext duck-typed 替身。一个内存 engine 同时种 05+06 表;searcher/rag fake;
    oracle_querier()->None(离线分支)。profile 带自定义敏感词验证 patterns 来自 profile。
    """

    def __init__(self, profile: Any, data_dir: Path) -> None:
        from sqlalchemy import create_engine

        self.profile = profile
        self._data_dir = data_dir
        engine = create_engine("sqlite://")
        _seed_lineage(engine)
        _seed_config(engine)
        self.engine = engine
        self.searcher = _FakeSearcher({
            "OrderService": [_sym("OrderService", 5, "app/OrderService.java",
                                  container="app.svc")],
        })
        self.rag_provider = _FakeRag()

    def oracle_querier(self) -> None:
        # 离线分支:lineage 类 tool 走降级(note='oracle_offline'),不真连 Oracle。
        return None

    def oracle_router(self) -> None:
        # Block 1b Task 13: evidence.py 已改用 oracle_router();
        # 离线测试返 None -> tools 走 router=None 降级(note='oracle_offline')。
        return None


@pytest.fixture
def evidence_app_ctx(make_profile, tmp_path: Path) -> _EvidenceAppCtx:
    """profile.config.sensitive_key_patterns 注入自定义词(证明 config tool 从 profile 取)。"""
    profile = make_profile(data_dir=tmp_path / "data")
    # 注入自定义敏感词到 profile(floor 默认不含 custom_secret_kw)
    profile.config.sensitive_key_patterns = list(
        profile.config.sensitive_key_patterns) + [_CUSTOM_SECRET_KW]
    # 注册 business_docs 子集名:WF3 起 server 挂了 InputValidationMiddleware,rag_search 的
    # corpora 须 ∈ profile 注册子集(红线 #9 host 不可信),否则被 middleware 拒。test_rag_search_tool 用
    # business_docs,故这里注册它(中性子集名,非真客户语料目录)。
    profile.config.corpus_subset_prefixes = {"business_docs": ["docs/business"]}
    return _EvidenceAppCtx(profile, tmp_path / "data")


@pytest.fixture
def evidence_server(evidence_app_ctx):
    # build_server 已在内部调 register_evidence_tools(13 证据 tool),不重复注册。
    return build_server(evidence_app_ctx)


# --------------------------------------------------------------------------- lineage tools


async def test_lookup_table_tool_offline(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool("lookup_table", {"table": "ORDERS"})
        assert res.data["table"] == "ORDERS"
        assert res.data["edges_out"] >= 1
        assert res.data["note"] == "oracle_offline"     # querier=None 离线分支


async def test_lookup_lineage_tool_offline(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool("lookup_lineage", {"table": "ORDERS"})
        assert any(d["table"] == "ORDER_ITEMS" for d in res.data["downstream"])
        assert res.data["note"] == "oracle_offline"


async def test_lookup_dependency_tool_offline(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool("lookup_dependency", {"name": "V_ORDERS"})
        assert res.data["name"] == "V_ORDERS"
        assert res.data["dependents"] == []
        assert res.data["note"] == "oracle_offline"


async def test_lookup_sequence_tool_offline(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool("lookup_sequence", {"name": "ORDER_SEQ"})
        assert res.data["name"] == "ORDER_SEQ"
        assert res.data["sequence"] is None
        assert res.data["note"] == "oracle_offline"


async def test_search_sql_tool(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool("search_sql", {"pattern": "FROM ORDERS"})
        rows = _list_payload(res)
        assert isinstance(rows, list)
        assert rows and rows[0]["template_id"] == "t1"


async def test_trace_method_dataflow_tool(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool("trace_method_dataflow", {"source_path": "X.java"})
        rows = _list_payload(res)
        assert isinstance(rows, list)
        # lineage_evidence 反查 e1 -> ORDERS/ORDER_ITEMS
        tables = {h["table"] for h in rows}
        assert "ORDERS" in tables and "ORDER_ITEMS" in tables


# --------------------------------------------------------------------------- config tools


async def test_lookup_config_tool(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool("lookup_config", {"config_key": "feature.flag.x"})
        assert res.data["config_key"] == "feature.flag.x"
        assert res.data["items"][0]["value_raw"] == "true"


async def test_lookup_config_tool_uses_profile_patterns(evidence_server):
    """关键(承接 WF2 安全修复):config tool 的 patterns 从 profile 取,非只靠 floor。

    description 含 adminpin=LEAKME9(adminpin 不含任何 floor 子串);若 patterns 真从
    profile.config.sensitive_key_patterns 来,LEAKME9 被 mask;否则(floor-only)会泄漏。
    """
    async with Client(evidence_server) as client:
        res = await client.call_tool("lookup_config", {"config_key": "feature.flag.x"})
        blob = repr(res.data)
        assert "LEAKME9" not in blob                      # profile 自定义词生效 -> mask
        assert _CUSTOM_SECRET_KW in res.data["items"][0]["description"]  # key 保留值打码


async def test_lookup_rule_tool(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool("lookup_rule", {"rule_set": "PricingRule"})
        assert res.data["rule_set"] == "PricingRule"
        assert res.data["category"] == "pricing"


async def test_trace_config_impact_tool(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool("trace_config_impact", {"entity_key": "feature.flag.x"})
        assert res.data["entity_key"] == "feature.flag.x"
        assert res.data["direct_bindings"]


async def test_explain_rule_logic_tool(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool("explain_rule_logic", {"rule_set_id": "rs1"})
        assert res.data["rule_set_id"] == "rs1"
        assert isinstance(res.data["bindings"], list)


async def test_diff_config_tool_missing_snapshot(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool(
            "diff_config", {"source_id": "s1", "env_a": "dev", "env_b": "prod"})
        assert res.data["note"] == "snapshot_missing"


# --------------------------------------------------------------------------- code / rag tools


async def test_search_code_tool(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool("search_code", {"query": "OrderService"})
        rows = _list_payload(res)
        assert isinstance(rows, list)
        assert rows and rows[0]["target"] == "app.svc.OrderService"


async def test_rag_search_tool(evidence_server):
    async with Client(evidence_server) as client:
        res = await client.call_tool(
            "rag_search",
            {"queries": {"zh": "计费配置", "en": "charge config"},
             "corpora": ["business_docs"]})
        rows = _list_payload(res)
        assert isinstance(rows, list)
        assert rows and rows[0]["doc"] == "app/charge.md"


# --------------------------------------------------------------------------- error handling


async def test_core_value_error_becomes_tool_error(evidence_server):
    """core 抛 ValueError(非法 identifier 分号)-> MCP 层转 ToolError(不裸传 traceback)。"""
    async with Client(evidence_server) as client:
        with pytest.raises(Exception):                    # ToolError 经 Client 抛
            await client.call_tool("lookup_table", {"table": "ORDERS; DROP TABLE X"})
