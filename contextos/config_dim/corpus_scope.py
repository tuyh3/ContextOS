"""corpus 子集 scoping(不串库): 把 corpus 子集名 -> path_prefixes, 只在这些前缀里 grep。

实现 03 §2.1 未落地的子集过滤; 复用 03b `recall/sparse.ripgrep_hits` 的 `path_prefixes`。

不串库关键(spec §5.1): 子目录不存在时返 `[]`(该子集贡献 0 命中), **不**退化为
全量搜 —— 否则 path D(RAG 业务文档)会串到 business_docs 之外的语料, 破坏子集隔离。
"""
from __future__ import annotations

from pathlib import Path

from contextos.recall.sparse import Hit, ripgrep_hits  # 复用 03b(-F 字面 + per -e + returncode 守护)


def subset_prefixes(subsets: list[str], prefix_map: dict[str, list[str]]) -> list[str]:
    """corpus 子集名列表 -> 对应 path_prefixes 的并集(按出现顺序, 不去重)。"""
    out: list[str] = []
    for s in subsets:
        out.extend(prefix_map.get(s, []))
    return out


def scoped_hits(
    patterns: list[str],
    materialized_dir: str | Path,
    subsets: list[str],
    prefix_map: dict[str, list[str]],
) -> list[Hit]:
    """只在指定 corpus 子集对应的 path_prefixes 里 grep -> 不串库。"""
    prefixes = subset_prefixes(subsets, prefix_map) or None
    # 仅保留真实存在的子目录(否则 ripgrep 报错), 不存在的子集 -> 该子集贡献 0(不退化为全量)
    if prefixes:
        prefixes = [p for p in prefixes if (Path(materialized_dir) / p).exists()]
        if not prefixes:
            return []
    return ripgrep_hits(patterns, materialized_dir, path_prefixes=prefixes)


def make_rag_search(
    materialized_dir: str | Path,
    prefix_map: dict[str, list[str]],
):
    """把 4 参 scoped_hits 包成 path_c/d_query 期望的 2 参 search(patterns, subsets)。

    pipeline 注入 `rag_search=make_rag_search(...)`, path_c_query / path_d_query 只需
    `search(patterns, subsets)` 即可在指定 corpus 子集里 grep(不串库, 子集隔离同 scoped_hits)。
    """
    def search(patterns: list[str], subsets: list[str]) -> list[Hit]:
        return scoped_hits(patterns, materialized_dir, subsets, prefix_map)
    return search
