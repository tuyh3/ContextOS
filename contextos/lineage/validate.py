"""Layer 9: 边 ID / 去重 / 校验(移植 LP validate.py)。

离线降级: resolver.has_metadata False -> 不丢边(只在元数据非空时做"双不存在则丢")。
边/证据用 dict(对齐 store.write_edges 行形态)。
"""
from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from contextos.lineage.name_resolve import NameResolver

_UNDIRECTED_TYPES = {"JOIN", "WHERE_EQ"}
_CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def make_edge_id(src_table: str, src_col: str, dst_table: str, dst_col: str,
                 relation_type: str, src_owner: str = "", dst_owner: str = "") -> str:
    """边身份锚含 owner(裁决 5 / review HIGH): 显式 schema 的同名表跨 owner 是不同边。

    owner 默认空 -> 裸名 SQL 行为不变(去重/方向语义保持)。"""
    if relation_type in _UNDIRECTED_TYPES:
        pair = sorted([(src_owner, src_table, src_col), (dst_owner, dst_table, dst_col)])
        key = f"{pair[0]}:{pair[1]}:{relation_type}"
    else:
        key = f"{src_owner}.{src_table}.{src_col}:{dst_owner}.{dst_table}.{dst_col}:{relation_type}"
    return "E" + hashlib.md5(key.encode()).hexdigest()[:12].upper()


def deduplicate_edges(edges: list[dict[str, Any]],
                      evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edge_map: dict[str, dict[str, Any]] = {}
    for edge in edges:
        eid = edge["edge_id"]
        if eid in edge_map:
            existing = edge_map[eid]
            existing["evidence_count"] = existing.get("evidence_count", 0) + edge.get("evidence_count", 0)
            if _CONF_RANK.get(edge.get("confidence", ""), 0) > _CONF_RANK.get(existing.get("confidence", ""), 0):
                existing["confidence"] = edge["confidence"]
        else:
            edge_map[eid] = dict(edge)
    ev_counts = Counter(ev["edge_id"] for ev in evidences)
    for eid, edge in edge_map.items():
        if eid in ev_counts:
            edge["evidence_count"] = ev_counts[eid]
    return list(edge_map.values())


def validate_edges(edges: list[dict[str, Any]],
                   resolver: NameResolver) -> tuple[list[dict[str, Any]], list[str]]:
    """返回 (validated_edges, unknown_tables)。离线(无元数据)不丢边。"""
    validated: list[dict[str, Any]] = []
    unknown: set[str] = set()
    offline = not resolver.has_metadata
    for edge in edges:
        src_t, dst_t = edge.get("src_table", ""), edge.get("dst_table", "")
        if offline:
            validated.append(edge)
            continue
        src_known = resolver.table_exists(src_t)
        dst_known = resolver.table_exists(dst_t)
        if not src_known and not dst_known:
            unknown.add(src_t)
            unknown.add(dst_t)
            continue
        if not src_known:
            edge["confidence"] = "low"
            unknown.add(src_t)
        if not dst_known:
            edge["confidence"] = "low"
            unknown.add(dst_t)
        if resolver.fk_pair(src_t, dst_t):
            edge["confidence"] = "high"
        validated.append(edge)
    return validated, sorted(t for t in unknown if t)
