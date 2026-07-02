# contextos/orchestrator/registry.py
"""可插拔 provider registry(08 §2)。加桥 = 注册新 runner,框架 0 改动。

CheapBridge.run(breakdown) -> ProviderResult(第一阶段召回桥)。
RerankBridge.run(breakdown, candidates) -> ProviderResult(第二阶段 07,消费候选池)。
build_default_registry 把五个异构 provider 入口闭包成统一 runner(各闭包自己的资源)。
注册序:05(db_lineage_bridge)先于 06(config_dimension_bridge)(盲区 2 / 构建契约 §3)。
G6:07 rerank 闭包**必须传 lookup=NullLookup()**(防 RAG 双算 = Plan 07 follow-up)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, cast

from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult

CheapRun = Callable[[object], ProviderResult]
RerankRun = Callable[[object, list[ProviderCandidate]], ProviderResult]


@dataclass
class CheapBridge:
    worker_name: str
    run: CheapRun


@dataclass
class RerankBridge:
    worker_name: str
    run: RerankRun


@dataclass
class ProviderRegistry:
    cheap_bridges: list[CheapBridge] = field(default_factory=list)
    rerank_bridge: RerankBridge | None = None

    def register_cheap(self, bridge: CheapBridge) -> None:
        self.cheap_bridges.append(bridge)            # 顺序保留(05 先于 06)

    def register_rerank(self, bridge: RerankBridge) -> None:
        self.rerank_bridge = bridge


def build_rag_query(breakdown) -> dict:
    """02 breakdown -> RAG 查询 dict(对齐 enricher.RagBusinessDocLookup._build_query 形态)。

    key_entities 合并 业务词 + 候选表名 + 候选配置键(review R2 HIGH 1):RagProvider sparse 用
    key_entities 当 ripgrep 字面 pattern,只传 business key_entities 会让 RAG 抓不到点名表名/键名
    的业务文档 -> rag 直接桥(投影特例)恒 miss。去重保序。
    corpora 是 03 §10 契约字段;RagProvider 当前未 enforce [v1 deferred -> Plan 03.5],按契约声明意图。
    """
    merged: list[str] = []
    seen: set[str] = set()
    for t in [*breakdown.key_entities,
              *(c.term for c in getattr(breakdown, "candidate_table_terms", []) or []),
              *(c.term for c in getattr(breakdown, "candidate_config_keys", []) or [])]:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            merged.append(t)
    return {
        "key_entities": merged,
        "matched_capabilities": [c.capability for c in breakdown.matched_capabilities],
        "queries": {"zh": breakdown.queries.zh, "en": breakdown.queries.en},
        "corpora": ["business_docs", "dict_docs"],
    }


def _files_from_code_result(result) -> list[str] | None:
    """从 code_search ProviderResult 抽候选源文件(给 05 D10 method->table;review R2 HIGH 2)。去重保序。"""
    if result is None:
        return None
    seen: set[str] = set()
    files: list[str] = []
    for c in result.candidates:
        f = c.signals.get("file")
        if f and f not in seen:
            seen.add(f)
            files.append(f)
    return files or None


def build_default_registry(*, searcher, rag_provider, lineage_engine, config_engine,
                           llm, rerank_config=None) -> ProviderRegistry:
    """把五桥闭包成统一 runner。资源(searcher / rag_provider / 两 engine / llm)由调用方建好传入。

    懒 import 真 provider 入口(避免 import 期拉重依赖;单测用 fake registry 不触发这里)。
    D10 wiring(review R2 HIGH 2):code_search 先注册先跑,把候选源文件存进 shared;
    db_lineage_bridge 闭包读 shared 当 `method_source_paths` -> 需求只点名方法/类(无显式表名)时
    05 仍能 method->table 反查出 SQL_TABLE。不改 CheapBridge 签名,靠注册序 + pipeline 按序跑。
    shared 是 per-registry 可变态:v1 单需求顺序跑 OK;并发请求须每请求 build 一个 registry(Plan 10)。
    """
    from contextos.code_intel.code_search.provider import search_code
    from contextos.config_dim.provider import search_config
    from contextos.lineage.provider import search_lineage
    from contextos.rerank import NullLookup, rerank

    shared: dict[str, list[str] | None] = {}

    def _code_run(bd):
        shared["code_files"] = None                           # 先重置:registry 复用 + 本次 code_search 异常时不串上次(review R3 MEDIUM 1)
        res = search_code(bd, searcher)
        shared["code_files"] = _files_from_code_result(res)   # 填给 05 D10
        return res

    reg = ProviderRegistry()
    reg.register_cheap(CheapBridge("code_search", _code_run))                       # 先跑,填 shared
    reg.register_cheap(CheapBridge("rag", lambda bd: rag_provider.search(build_rag_query(bd))))
    reg.register_cheap(CheapBridge("db_lineage_bridge",
        lambda bd: search_lineage(  # bd: object(provider-agnostic CheapRun);真路径恒 RequirementBreakdown
            cast(Any, bd), lineage_engine, method_source_paths=shared.get("code_files"))))
    reg.register_cheap(CheapBridge("config_dimension_bridge", lambda bd: search_config(bd, config_engine)))
    reg.register_rerank(RerankBridge(
        "llm_rerank",
        lambda bd, cands: rerank(bd, cands, llm, lookup=NullLookup(), config=rerank_config)))  # G6
    return reg
