"""SQL/config 维候选的业务文档摘要(对接 03 RAG)。

BusinessDocLookup 是 07 依赖的接口(不直接耦合 RagProvider);RagBusinessDocLookup 是
生产实现(包一个 RagProvider);NullLookup 给 method 维 / RAG 不可用时用。测试用 Fake。
任何异常 fail-safe 返空摘要 —— RAG 不可用不能阻塞投票(对齐 design §6 失败处理精神)。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BusinessDocLookup(Protocol):
    def lookup(self, candidate, breakdown) -> str:
        """返回该候选的业务文档摘要(<= max_chars);无 / 失败返 ''。"""
        ...


class NullLookup:
    """不查 RAG,永远返空摘要。method 维 + RAG 未配置时用。"""

    def lookup(self, candidate, breakdown) -> str:
        return ""


class RagBusinessDocLookup:
    """真接 03 RAG:用候选名末段 + 需求业务词查 RagProvider,取 top-K snippet 拼摘要。

    rag_provider 只需有 search(query: dict) -> ProviderResult(snippet 在 candidate.signals)。
    """

    def __init__(self, rag_provider, *, top_k: int = 3, max_chars: int = 500) -> None:
        self._rag = rag_provider
        self._k = top_k
        self._max = max_chars

    def lookup(self, candidate, breakdown) -> str:
        # 安全信任模型(对齐 design §7 双保险): snippet 一靠上游 03 语料物化期 curate
        # (LeakageGate 正则排除点名改动文件, 如 change-log / gold), 二靠 07 prompt 组装层
        # redact_credentials 内容级兜底(在 provider._vote_chunk, 覆盖所有 lookup 实现)。
        try:
            res = self._rag.search(self._build_query(candidate, breakdown))
            snippets = [c.signals.get("snippet", "") for c in res.candidates[: self._k]]
            return "\n".join(s for s in snippets if s)[: self._max]
        except Exception:  # fail-safe:RAG 挂了不阻塞投票
            return ""

    def _build_query(self, candidate, breakdown) -> dict:
        # matched_capabilities 当 path 前缀(RagProvider 按能力模块目录过滤)。
        # corpora 按 03 §10 契约传静态业务子集 —— 03 集中定义子集 = SSOT, 07 是声明的调用方之一
        # (03 §2.1: "调用方 05/06/07/08 传 corpora")。[v1 deferred -> Plan 03.5]: 当前
        # RagProvider.search 未读 corpora(子集隔离暂由 06 corpus_scope 代偿), 03.5 接入后才真过滤;
        # 此处先按契约声明意图(守 docs/CLAUDE.md §3.7.5: deferred 也把契约字段就地写出, 不让正文
        # 像"07 无 corpus 范围"——防 SQL/config 维从 change-log / 历史 AI 反哺召回的漂移)。
        name = candidate.target.split(".")[-1].split("#")[-1]
        ents = [name, *breakdown.key_entities]
        return {
            "key_entities": [e for e in ents if e],
            "matched_capabilities": [c.capability for c in breakdown.matched_capabilities],
            "queries": {"zh": breakdown.queries.zh, "en": breakdown.queries.en},
            "corpora": ["business_docs", "dict_docs"],   # 03 §10 静态业务语料默认子集(不传也是此默认)
        }
