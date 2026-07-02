"""InputValidationMiddleware 硬隔离测试(Plan 10 Task 9,红线 #8/#9)。

设计思路
--------
mcp_server/middleware.py 的 InputValidationMiddleware 是 FastMCP middleware,挂在
build_server 返回的 server 上(mcp.add_middleware),所有 tool call 在进入 tool body
之前先过一道**粗粒度**输入隔离(host 不可信,红线 #9)。它拦三类:

1. ad-hoc corpus(红线 #9 host 不可信):凡 arguments 带 `corpora`,每个值必须 ∈ profile
   注册的 corpus 子集枚举(profile.config.corpus_subset_prefixes 的键)。非注册值即拒
   (host 不能塞一个临时语料路径让 RAG 去搜未授权目录)。
2. 连接注入(红线 #4/#9):arguments 含 tns/dsn/connection/db_url/conn 等连接键即拒
   (Oracle 连接由 profile/env 定,host 绝不能注入 —— 防绕过白名单连生产库)。
3. 非只读 SQL(红线 #4):带 sql/SQL 形态参数的 tool(如 search_sql 误传 SQL 串),过
   oracle_gate.assert_query_is_readonly;非 SELECT/WITH、多语句、forbidden keyword 即拒。

这是 design §5 的「第一层」(粗粒度 middleware),与 evidence.py tool body 内的脱敏 /
core 的 identifier 校验(第二层)互补。middleware 早拒可以在 tool 跑之前就挡掉,且对
13 个 tool 统一生效(不必每个 tool body 重复写 corpora 白名单逻辑)。

评分标准
--------
- ad-hoc corpus 被拒:corpora=["__evil_adhoc__"] -> Client 收到错误(ToolError 经
  isError 抛 Exception),tool body 不执行。
- 合法 corpus 放行:corpora=["business_docs"](profile 已注册)-> 正常返回 list,
  证明 middleware 不误伤注册值。
- 连接注入被拒:任意 tool 带 tns="PROD_DB" -> 被拒(连 lookup_table 这种本不吃 tns
  的 tool,host 多塞一个连接键也挡掉)。
- 非只读 SQL 被拒:search_sql 的 pattern 传一条 DELETE 语句 -> 被拒(防 host 把
  search_sql 当任意 SQL 通道)。
- 普通调用不受影响:不带上述危险键的合法 tool call(lookup_table table=ORDERS)正常返回。

测试 fixture 用中性合成名(business_docs / ORDERS / feature.flag.x),不掺真客户
schema/owner/表名/实例名(守 feedback_offline_test_neutral_fixtures)。corpora 白名单
取自 profile.config.corpus_subset_prefixes(本测试注册 business_docs / dict_docs 两个
中性子集名)。

自动脚本逻辑
------------
_ValidatedAppCtx = AppContext duck-typed 替身:真内存 SQLite engine 种 05 lineage 行
(lookup_table/search_sql 有真数据走通)+ FakeRag(rag_search 返一条命中)+ profile
注册 business_docs。build_server(app_ctx) 内部已 add_middleware(InputValidationMiddleware),
故用 Client(server) 调 tool 时 middleware 自动生效。pytest.raises(Exception) 捕获经
Client 抛出的 ToolError(被拒);正常 res.data / structured_content 断言放行。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from contextos.lineage import store as lineage_store
from contextos.mcp_server.middleware import InputValidationMiddleware, _QUERY_MAX
from contextos.mcp_server.server import build_server

# profile 注册的中性 corpus 子集名(非真客户语料目录;只用作白名单枚举)。
_REGISTERED_CORPORA = {"business_docs": ["docs/business"], "dict_docs": ["docs/dict"]}


def _list_payload(res: Any) -> list[Any]:
    """list 返回型 tool 的真数据(FastMCP 对 list[Any] 元素无精确 schema,走
    structured_content['result'];与 test_evidence_tools 同一读法)。"""
    return res.structured_content["result"]


def _seed_lineage(engine: Any) -> None:
    """中性合成血缘 + SQL 模板(lookup_table / search_sql 走通需要真行)。"""
    lineage_store.create_all(engine)
    with engine.begin() as c:
        c.execute(lineage_store.lineage_edges.insert(), [
            {"edge_id": "e1", "src_owner": "APP", "src_table": "ORDERS",
             "dst_owner": "APP", "dst_table": "ORDER_ITEMS",
             "relation_type": "JOIN", "confidence": "high", "evidence_count": 2},
        ])
        c.execute(lineage_store.sql_templates.insert(), [
            {"template_id": "t1", "source_file": "X.java", "container": "XSvc.run",
             "sql_text": "SELECT * FROM ORDERS WHERE ID=?",
             "recovery_mode": "sql_file", "confidence": "high"},
        ])


class _FakeRag:
    """RagProvider 替身:search 返一条 canned 命中(合法 corpus 放行后能拿到 list)。"""

    def search(self, query: dict) -> Any:
        from contextos.orchestrator.provider_io import (
            ProviderCandidate,
            ProviderResult,
        )

        return ProviderResult(
            worker_name="rag",
            score=0.9,
            candidates=[ProviderCandidate(
                target="docs/business/charge.md", kind="BUSINESS_DOC",
                signals={"rerank_score": 0.9, "snippet": "charge config"})],
        )


class _ValidatedAppCtx:
    """AppContext duck-typed 替身:真 engine(种 05 行)+ FakeRag + 注册 corpora。"""

    def __init__(self, profile: Any) -> None:
        from sqlalchemy import create_engine

        self.profile = profile
        engine = create_engine("sqlite://")
        _seed_lineage(engine)
        self.engine = engine
        self.rag_provider = _FakeRag()
        self.searcher = None  # 本测试不打 search_code,不需要真 searcher

    def oracle_querier(self) -> None:
        return None  # 离线分支:lineage 类 tool 走降级

    def oracle_router(self) -> None:
        # Block 1b Task 13: evidence.py 已改用 oracle_router(); 离线测试返 None。
        return None


@pytest.fixture
def validated_app_ctx(make_profile, tmp_path: Path) -> _ValidatedAppCtx:
    profile = make_profile(data_dir=tmp_path / "data")
    profile.config.corpus_subset_prefixes = dict(_REGISTERED_CORPORA)
    return _ValidatedAppCtx(profile)


@pytest.fixture
def validated_server(validated_app_ctx):
    # build_server 内部已 add_middleware(InputValidationMiddleware(app_ctx))。
    return build_server(validated_app_ctx)


# ----------------------------------------------------------- 1. ad-hoc corpus 拦截


async def test_ad_hoc_corpus_rejected(validated_server):
    """corpora 带未注册子集名 -> middleware 拒(红线 #9 不接 ad-hoc corpus)。"""
    async with Client(validated_server) as client:
        with pytest.raises(Exception):  # ToolError 经 Client 抛
            await client.call_tool(
                "rag_search",
                {"queries": {"zh": "x", "en": "x"}, "corpora": ["__evil_adhoc__"]})


async def test_ad_hoc_corpus_rejected_when_mixed_with_registered(validated_server):
    """一个注册 + 一个未注册混传 -> 仍拒(白名单是全集约束,不是任一命中即可)。"""
    async with Client(validated_server) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "rag_search",
                {"queries": {"zh": "x", "en": "x"},
                 "corpora": ["business_docs", "__evil_adhoc__"]})


async def test_registered_corpus_passes(validated_server):
    """corpora 全是注册子集 -> 放行,正常返回 list(不误伤合法值)。"""
    async with Client(validated_server) as client:
        res = await client.call_tool(
            "rag_search",
            {"queries": {"zh": "charge", "en": "charge"}, "corpora": ["business_docs"]})
        rows = _list_payload(res)
        assert isinstance(rows, list)


# ----------------------------------------------------------- 2. 连接注入拦截


async def test_connection_injection_tns_rejected(validated_server):
    """host 给任意 tool 多塞 tns 键 -> 拒(连接由 profile/env 定,红线 #4/#9)。"""
    async with Client(validated_server) as client:
        with pytest.raises(Exception):
            await client.call_tool("lookup_table", {"table": "ORDERS", "tns": "PROD_DB"})


async def test_connection_injection_dsn_rejected(validated_server):
    """dsn 键同样被拒(连接键黑名单覆盖 tns/dsn/connection/db_url/conn)。"""
    async with Client(validated_server) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "lookup_table",
                {"table": "ORDERS", "dsn": "host:1521/PRD"})


# ----------------------------------------------------------- 3. 非只读 SQL 拦截


async def test_non_readonly_sql_in_pattern_rejected(validated_server):
    """search_sql 的 pattern 传一条 DML 语句 -> 过 readonly 闸门被拒(防 SQL 通道滥用)。"""
    async with Client(validated_server) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "search_sql", {"pattern": "DELETE FROM ORDERS WHERE 1=1"})


async def test_plain_pattern_not_treated_as_sql(validated_server):
    """普通字面 pattern(非 SQL 串)放行 —— readonly 闸门只对像 SQL 的参数生效,
    不把每个 grep 字面 pattern 都当 SQL 拒(否则 search_sql 主用法全废)。"""
    async with Client(validated_server) as client:
        res = await client.call_tool("search_sql", {"pattern": "FROM ORDERS"})
        rows = _list_payload(res)
        assert isinstance(rows, list)
        assert rows and rows[0]["template_id"] == "t1"


# ----------------------------------------------------------- 普通调用不受影响


async def test_benign_call_passes(validated_server):
    """不带任何危险键的合法 tool call 正常返回(middleware 不拦正常路径)。"""
    async with Client(validated_server) as client:
        res = await client.call_tool("lookup_table", {"table": "ORDERS"})
        assert res.data["table"] == "ORDERS"
        assert res.data["edges_out"] >= 1


# ----------------------------------------------------------- 5. query 长度上限(Task 5)


class _Ctx:
    pass


def _mw():
    # app_ctx 仅 _registered_corpora 用到; query 长度检查不碰它, 传占位即可
    return InputValidationMiddleware(_Ctx())


def test_query_within_limit_ok():
    _mw()._check_query_length({"query": "x" * _QUERY_MAX})   # 不抛


def test_query_too_long_rejected():
    with pytest.raises(ToolError):
        _mw()._check_query_length({"query": "x" * (_QUERY_MAX + 1)})


def test_query_absent_ok():
    _mw()._check_query_length({"fqn": "com.example.app.X"})   # 无 query 键 -> 放行
