"""RAG 检索的 tool 形态适配(Plan 10 §证据 tool)。

rag_provider.py 的 RagProvider.search 吃完整 provider query dict(queries /
key_entities / matched_capabilities / corpora),供 08 编排用;本模块是【tool 形态】=
给 (queries, corpora, top_k) 直接检索, 返回纯 dict list, 给 MCP/CLI/Python lib 共用
(core tools.py 只查、返纯 dict, 不碰 MCP 协议)。

复用 RagProvider.search:把入参拼成 provider query dict(key_entities 从 queries
文本派生, sparse 检索靠它)。返回 [{doc, passage, score, corpus}]。corpora 必须
∈ 已注册子集 —— 校验留 middleware(WF3), 本函数假设已校验。
"""
from __future__ import annotations

from typing import Protocol

from contextos.orchestrator.provider_io import ProviderResult


class _Searchable(Protocol):
    def search(self, query: dict) -> ProviderResult: ...


def _derive_key_entities(queries: dict) -> list[str]:
    """从 queries 的 zh/en 文本按空白切词派生 sparse pattern(去重保序)。"""
    seen: dict[str, None] = {}
    for text in (queries or {}).values():
        for tok in str(text).split():
            t = tok.strip()
            if t:
                seen.setdefault(t, None)
    return list(seen.keys())


def rag_search(rag_provider: _Searchable, *, queries: dict, corpora: list[str],
               top_k: int = 10,
               corpus_prefixes: dict[str, list[str]] | None = None) -> list[dict]:
    """复用 RagProvider.search(corpora 名字由 middleware 校验)。

    corpus scoping(WF3 security 修复): corpus_prefixes(profile.config.corpus_subset_prefixes,
    子集名 -> path prefixes)把请求的 corpora 解析成 path prefixes 传给 provider 限 grep 范围,
    不串别的子集; provenance 按命中 doc 的 rel_path 反推真实子集,不伪造成 corpora[0]。映射缺失
    时退化:scope 不限 + 单 corpus 归该 corpus / 多 corpus 留空(不伪造)。MCP 路径总传映射。

    queries: {"zh": ..., "en": ...} 自由文本;全空 -> 不打 provider, 返回 []。
    返回:    [{doc, passage, score, corpus}](按 provider score 降序, 截 top_k)。
    """
    key_entities = _derive_key_entities(queries)
    if not key_entities:
        return []

    cp = corpus_prefixes or {}
    # 请求的 corpora -> path prefixes(去重保序), 限 provider grep 范围(corpus scope)
    path_prefixes: list[str] = []
    seen: set[str] = set()
    for name in corpora:
        for pfx in cp.get(name, []):
            if pfx not in seen:
                seen.add(pfx)
                path_prefixes.append(pfx)

    result = rag_provider.search({
        "queries": queries,
        "key_entities": key_entities,
        "matched_capabilities": [],
        "path_prefixes": path_prefixes,        # 接通 RagProvider path scope(_search_sparse 优先用它)
        "corpora": corpora,
    })

    rows: list[dict] = []
    for c in result.candidates[:top_k]:
        rows.append({
            "doc": c.target,
            "passage": str(c.signals.get("snippet", "")),
            "score": float(c.signals.get("rerank_score", 0.0)),
            "corpus": _corpus_of(c.target, corpora, cp),
        })
    return rows


def _corpus_of(rel_path: str, corpora: list[str], cp: dict[str, list[str]]) -> str:
    """命中 doc 的 rel_path 按 corpus_subset_prefixes 反推真实子集名(不伪造)。

    有映射: rel_path 落在哪个子集的 prefix 下就标哪个。无映射 + 单 corpus: 命中必来自唯一
    请求子集 -> 标它。无映射 + 多 corpus: 无法可靠归属 -> 留空(不伪造成 corpora[0])。
    """
    for name in corpora:
        for pfx in cp.get(name, []):
            p = pfx.rstrip("/")
            if rel_path == p or rel_path.startswith(p + "/"):
                return name
    if not cp and len(corpora) == 1:
        return corpora[0]
    return ""
