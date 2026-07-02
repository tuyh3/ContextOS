"""配置维度 owner overlay —— scoped 双边 owner 解析 + 冲突 ambiguous(spec §5.2 + HIGH 1 R3)。

设计:
- 不改 05(数据库血缘)。owner 解析结果落 config_dim.schema.owner_resolution overlay 表,
  复合 PK (edge_id, module, datasource_key) —— owner 解析是 module/datasource 级,
  不同 scope 并存不互覆盖。
- 直连客户 schema: owner=连接用户(direct, medium)。
- 通用账户(如 ET)经 Oracle public synonym 间接访问: owner=REAL_OWNER(synonym, high)。
  synonym 查走 05 在线(注入 synonym_lookup, 真实 = 查 DBA_SYNONYMS; 单测 fake)。
- resolve_edge_owner: 无 module 上下文 + 多 owner -> 'ambiguous'(绝不强行给一个);
  给 module -> 该 scope 内确定 owner。
"""
from __future__ import annotations

from sqlalchemy import select, insert
from sqlalchemy.engine import Engine

from contextos.config_dim.schema import owner_resolution


def resolve_side(conn_user: str, table: str, synonym_lookup) -> dict:
    """单边 owner 解析。

    直连客户 owner=连接用户; 间接(通用账户)走 Oracle synonym -> REAL_OWNER。
    synonym_lookup(user, table) -> owner | None(注入; 真实 = 05 在线查 DBA_SYNONYMS)。
    返回 {"db", "owner", "source", "confidence"}。
    """
    real = None
    try:
        real = synonym_lookup(conn_user, table)
    except Exception:
        real = None
    if real:
        return {"db": "", "owner": real, "source": "synonym", "confidence": "high"}
    if conn_user:
        return {"db": "", "owner": conn_user, "source": "direct", "confidence": "medium"}
    return {"db": "", "owner": "", "source": "", "confidence": "needs_review"}


def write_resolution(engine: Engine, edge_id, module, datasource_key, src, dst,
                     schema_fingerprint="", resolved_at="") -> None:
    """按 scoped key (edge_id, module, datasource_key) 写一行 overlay(不改 05)。"""
    with engine.begin() as c:
        c.execute(insert(owner_resolution).values(
            edge_id=edge_id, module=module or "", datasource_key=datasource_key or "",
            resolved_src_db=src["db"], resolved_src_owner=src["owner"],
            src_resolution_source=src["source"], src_confidence=src["confidence"],
            resolved_dst_db=dst["db"], resolved_dst_owner=dst["owner"],
            dst_resolution_source=dst["source"], dst_confidence=dst["confidence"],
            schema_fingerprint=schema_fingerprint, resolved_at=resolved_at))


def resolve_edge_owner(engine: Engine, edge_id: str, side: str = "src", module: str | None = None):
    """无 module -> 多 owner 冲突返 'ambiguous'(HIGH 1 R3, 绝不强行给一个); 给 module -> 该 scope owner。"""
    col = owner_resolution.c.resolved_src_owner if side == "src" else owner_resolution.c.resolved_dst_owner
    q = select(col, owner_resolution.c.module).where(owner_resolution.c.edge_id == edge_id)
    with engine.connect() as c:
        rows = c.execute(q).fetchall()
    if module is not None:
        owners = {r[0] for r in rows if r.module == module and r[0]}
        return next(iter(owners), "")
    owners = {r[0] for r in rows if r[0]}
    if len(owners) > 1:
        return "ambiguous"
    return next(iter(owners), "")
