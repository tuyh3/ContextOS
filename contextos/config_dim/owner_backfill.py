"""W6: 06 -> 05 owner overlay 回填(spec §5.2 + 05 §12.4)。遍历 05 裸名边 -> module ->
datasource 连接身份 -> resolve_side(注入 synonym_lookup)-> write_resolution。不改 05。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Engine

from contextos.lineage import store as L
from contextos.config_dim.owner_overlay import resolve_side, write_resolution


def module_hint(evidence_ref: str) -> str:
    """evidence_ref='cust/impl/Dao.java:42' -> 'cust'(同 Plan 05 source_path.split('/')[0])。"""
    src = (evidence_ref or "").split(":")[0]
    return src.split("/")[0] if src else ""


def _edge_modules(engine_05: Engine, edge_id: str) -> list[str]:
    """该 edge 的全部 distinct module(一 edge 多 evidence -> 多源文件 -> 多 module)。
    HIGH 1(R3): scoped owner_resolution 复合 PK=(edge_id,module,datasource_key)正是为多
    module 消歧; 绝不能只取首条 evidence(那会一 edge 一 resolution, 打掉 scoped 设计,
    且首条是哪条还看 rowid 不确定)。"""
    with engine_05.connect() as c:
        evs = c.execute(select(L.lineage_evidence.c.evidence_ref).where(
            L.lineage_evidence.c.edge_id == edge_id)).fetchall()
    mods = {module_hint(ev.evidence_ref) for ev in evs}
    return sorted(m for m in mods if m)


def backfill_owners(engine_05: Engine, datasource_map: dict, synonym_lookup, engine_06: Engine) -> int:
    """对 05 裸名边(src/dst_owner='')回填 owner_resolution。返回写入(edge,module)条数。

    一 edge 可来自多 module 的 evidence -> 各 module 一条 scoped resolution(复合 PK 不互覆盖)。
    """
    with engine_05.connect() as c:
        edges = c.execute(select(L.lineage_edges).where(
            (L.lineage_edges.c.src_owner == "") | (L.lineage_edges.c.dst_owner == ""))).fetchall()
    n = 0
    for e in edges:
        for module in _edge_modules(engine_05, e.edge_id):   # 每 distinct module 一条
            conn = datasource_map.get(module)
            if not conn:
                continue  # 该 module 无 datasource 映射 -> 留空(离线/无配置)
            user = conn.get("user", "")
            dskey = conn.get("datasource_key", "")
            src = resolve_side(user, e.src_table, synonym_lookup) if e.src_owner == "" else \
                {"db": e.src_db, "owner": e.src_owner, "source": "static", "confidence": "high"}
            dst = resolve_side(user, e.dst_table, synonym_lookup) if e.dst_owner == "" else \
                {"db": e.dst_db, "owner": e.dst_owner, "source": "static", "confidence": "high"}
            write_resolution(engine_06, e.edge_id, module, dskey, src, dst)
            n += 1
    return n
