"""Task C1 — owner_overlay scoped 双边解析 + 冲突 ambiguous。

设计思路:
- resolve_side: 单边(src 或 dst)的 owner 解析。通用账户(如 ET)走 Oracle synonym ->
  REAL_OWNER(间接, source=synonym, high); 直连业务 schema(synonym 无)-> owner=连接用户
  (source=direct, medium); 无连接用户 -> needs_review。synonym_lookup 注入(真实 = 05 在线
  查 DBA_SYNONYMS), 单测用 fake lambda。
- write_resolution: 按 scoped key (edge_id, module, datasource_key) 写一行 overlay,
  不改 05。同 edge_id 不同 module/datasource 两条并存(复合 PK)。
- resolve_edge_owner: 无 module 上下文 + 多 owner -> 'ambiguous'(HIGH 1 R3, 绝不强行给一个);
  给 module -> 该 scope 内确定 owner。

评分标准: 两个断言全过即 green。
1. test_resolve_side_synonym_then_direct: synonym 命中走间接 owner; 无 synonym 走直连用户。
2. test_scoped_no_overwrite_and_conflict_ambiguous: 同 edge 双 scope 并存; 无 module 冲突 ->
   ambiguous; 指定 module -> 确定 owner。
"""
from sqlalchemy import create_engine

from contextos.config_dim.schema import metadata
from contextos.config_dim.owner_overlay import (
    resolve_side,
    write_resolution,
    resolve_edge_owner,
)


def test_resolve_side_synonym_then_direct():
    # 通用账户 + synonym -> REAL_OWNER(间接)
    side = resolve_side(conn_user="ET", table="CB_CUSTOMER", synonym_lookup=lambda u, t: "PARTY")
    assert side["owner"] == "PARTY" and side["source"] == "synonym"
    # 直连业务 schema(synonym 无)-> owner=连接用户
    side2 = resolve_side(conn_user="UPC", table="PM_OFFER", synonym_lookup=lambda u, t: None)
    assert side2["owner"] == "UPC" and side2["source"] == "direct"


def test_scoped_no_overwrite_and_conflict_ambiguous():
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    # 同 edge_id, 不同 module/datasource, 解到不同 owner -> 两条并存不互覆盖
    write_resolution(eng, edge_id="E1", module="crm", datasource_key="dsA",
                     src={"db": "", "owner": "UPC", "source": "direct", "confidence": "medium"},
                     dst={"db": "", "owner": "UPC", "source": "direct", "confidence": "medium"})
    write_resolution(eng, edge_id="E1", module="sec", datasource_key="dsB",
                     src={"db": "", "owner": "SEC", "source": "direct", "confidence": "medium"},
                     dst={"db": "", "owner": "SEC", "source": "direct", "confidence": "medium"})
    # query 无 module 上下文 -> 冲突 -> ambiguous
    assert resolve_edge_owner(eng, "E1", side="src") == "ambiguous"
    # 给定 module -> 确定
    assert resolve_edge_owner(eng, "E1", side="src", module="crm") == "UPC"
