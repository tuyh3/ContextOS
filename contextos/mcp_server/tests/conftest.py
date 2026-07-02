"""mcp_server 测试夹具。

`make_profile`: 合成一个**中性值** Profile(不掺真客户 schema/owner/db/实例名),
data_dir 默认指向 tmp_path 下的隔离目录。形态照搬 contextos/storage/tests/
test_db_profile.py 既有的 _profile() 中性范式(同一套 9 namespace 必填字段),
供 Plan 10 mcp_server 各 task 复用(AppContext / server / evidence / meta)。

`fake_app_ctx`: 一个 AppContext 形态的轻量替身(duck-typed,不起真 JDT/Oracle/RAG),
暴露 build_default_registry + analyze 所需的 5 个属性(profile/llm/searcher/
rag_provider/engine)。让 build_impact_map 端到端串通 02 breakdown -> 编排 ->
ImpactMap,而无需真重资源(Task 3 高风险闭环 + 后续 evidence/meta task 复用)。

oracle 命名空间填的 TEST_DB1 是白名单**枚举占位**(与真客户连接无关:
connect_from_profile 在缺 ORACLE_<TNS>_USER/_PASSWORD 凭据时即 OracleSafetyError,
不会真连网),allowed_instances 至少 1 项是 schema min_length 约束所需。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from contextos.llm import FakeLLM
from contextos.profile.schema import Profile


@pytest.fixture
def make_profile(tmp_path: Path):
    """返回一个工厂:make_profile(data_dir=...) -> 中性合成 Profile。

    不传 data_dir 时落在 tmp_path/contextos-data(测试隔离)。所有路径/实例名
    都是合成中性值,绝不含真客户标识(守 feedback_offline_test_neutral_fixtures)。
    """

    def _make(*, data_dir: Path | None = None) -> Profile:
        dd = data_dir if data_dir is not None else (tmp_path / "contextos-data")
        return Profile(**{
            "llm": {"provider": "test_llm", "api_key_env": "MCP_TEST_LLM_KEY"},
            "embedding": {"model": "test-embed"},
            "reranker": {"enabled": True, "model": "test-rerank",
                         "top_k_input": 50, "top_k_output": 10},
            "query_expansion": {"enabled": True,
                                "translation_provider": "main_llm",
                                "fallback_provider": "local"},
            "storage": {"data_dir": str(dd)},
            "ingestion": {"default_cleanup": "full",
                          "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
            "jdtls_runtime": {"jdtls_path": "/jdtls",
                              "lombok_path": "/jdtls/lombok.jar",
                              "java_home": "/jre"},
            "oracle": {"tns_admin": "/tns",
                       "allowed_instances": ["TEST_DB1"]},
            "projects": [{"name": "proj", "path": "/proj",
                          "language": "java", "build_system": "gradle"}],
        })

    return _make


# --------------------------------------------------------------------------
# fake_app_ctx: 端到端串通 build_impact_map 的轻量 AppContext 替身
# --------------------------------------------------------------------------

class _FakeSearcher:
    """SymbolSearcher 协议替身:workspaceSymbol 恒返空 -> code_search miss(不起 JDT)。"""

    def request_workspace_symbol(self, query: str) -> list[Any]:
        return []


class _FakeRag:
    """RagProvider 替身:search 恒返 miss(不起向量库 / reranker)。"""

    def search(self, query: dict) -> Any:
        from contextos.orchestrator.provider_io import ProviderResult

        return ProviderResult.miss("rag", "fake_no_corpus")


def _empty_lineage_config_engine():
    """真内存 SQLite + 05/06 空表(create_all)。

    lineage/config 桥读真表(空)-> miss,不崩(对齐 05/06 离线测试范式;
    比让 fake 抛异常靠 pipeline try/except 兜底更真、更干净)。red line #6:走
    SQLAlchemy engine,不裸 sqlite3。
    """
    from sqlalchemy import create_engine

    from contextos.config_dim import schema as config_schema
    from contextos.lineage import store as lineage_store

    engine = create_engine("sqlite://")
    lineage_store.create_all(engine)
    config_schema.metadata.create_all(engine)
    return engine


def _scope_in_scope() -> str:
    return json.dumps({"verdict": "in_scope", "reason": "x"}, ensure_ascii=False)


def _extract_grounded() -> str:
    # source_span 都落在 "新增动态计费批量操作,完成后发短信" 内 -> grounding 全过 -> ok
    return json.dumps({
        "business_intent": "新增动态计费批量操作",
        "key_entities": ["动态计费"],
        "actions": ["add"],
        "candidate_code_names": [
            {"term": "DynamicCharging", "kind": "camelcase", "source": "llm",
             "source_span": "动态计费"}],
        "candidate_table_terms": [
            {"term": "BILLING", "kind": "entity", "source": "llm", "source_span": "计费"}],
        "candidate_config_keys": [
            {"term": "批量上限", "kind": "param_term", "source": "llm", "source_span": "批量"}],
    }, ensure_ascii=False)


def _classify() -> str:
    return json.dumps({"matched_capabilities": [
        {"capability": "billing-charging", "confidence": 0.9, "evidence": "动态计费"}]},
        ensure_ascii=False)


def _translate() -> str:
    return json.dumps({"zh": "新增动态计费批量操作", "en": "Add bulk dynamic charging"},
                      ensure_ascii=False)


class _FakeAppCtx:
    """AppContext duck-typed 替身。属性形态对齐真 AppContext(profile/llm/searcher/
    rag_provider/engine),供 build_default_registry + analyze 消费。

    llm 用 FakeLLM(4 响应队列:scope/extract/classify/translate)。ok 路径耗 4 条;
    rejected 路径(prefilter 早退)耗 0 条。rerank 因空候选池在调 LLM 前 miss,不耗响应。
    """

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.llm = FakeLLM(responses=[
            _scope_in_scope(), _extract_grounded(), _classify(), _translate()])
        self.searcher = _FakeSearcher()
        self.rag_provider = _FakeRag()
        self.engine = _empty_lineage_config_engine()


@pytest.fixture
def fake_app_ctx(make_profile) -> _FakeAppCtx:
    """轻量 AppContext 替身(function-scoped,FakeLLM 队列每测试新鲜)。"""
    return _FakeAppCtx(make_profile())
