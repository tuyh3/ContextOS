"""Block 1a 核心: dependencies 表(ALL_DEPENDENCIES)-> lineage_edges 对象血缘边。

设计点:
- 边: src = view/proc/trigger(owner.table), dst = table(owner.table),
  edge_kind="OBJECT_DEPENDENCY", relation_type="" 留空(design §10, 绝不套 8 类),
  lineage_type="DIRECT", confidence="high"(系统级等同 FK),
  src_dataset_type = 引用方对象类型(VIEW/PROCEDURE/TRIGGER), dst_dataset_type="TABLE"。
- 证据: evidence_type="OBJECT_DEPENDENCY"(自由 String 非枚举), evidence_ref="ALL_DEPENDENCIES"。
- 归一: 复用 NameResolver(synonym 展开 + owner 归一到 owner.table 身份锚, 避开 K5)。
- 幂等: 先 clear_object_edges(只清自己 kind, 不碰静态 SQL 边)。
- 必须在 build_lineage(静态)之后跑(后者 clear_all 会清掉对象边), 见 plan Architecture。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

from contextos.lineage import store
from contextos.lineage.name_resolve import NameResolver
from contextos.lineage.validate import make_edge_id
from contextos.profile.schema import TablesConfig

EXTRACTOR_VERSION = "05.1a.0"

# 引用方对象类型(进 src_dataset_type): 这些对象 -> table 才产对象血缘边。
_REFERENCING_TYPES = {"VIEW", "PROCEDURE", "FUNCTION", "PACKAGE", "PACKAGE BODY", "TRIGGER"}
# 被引用方类型: 只关心指向 table/view 的依赖(指向 sequence/index 等不产表血缘边)。
_REFERENCED_TYPES = {"TABLE", "VIEW"}


def build_object_lineage(engine: Engine, tables_cfg: TablesConfig, *, now: str,
                         dblink_index: dict[str, str] | None = None) -> dict[str, Any]:
    """读 dependencies 表 -> 对象血缘边进 lineage_edges + 证据进 lineage_evidence。

    Block 1b: dblink_index={DBLINK_NAME: 目标库 TNS} -> 跨库依赖产 src_db != dst_db 边;
    dblink_index=None(默认) -> 老行为(有 referenced_link_name 的行直接 skip, 不产边)。
    解不出的 dblink 登记 unresolved_dblinks 并 skip(不产错库边)。
    """
    store.create_all(engine)
    store.clear_object_edges(engine)              # 幂等: 重建前清自己 kind(edges)
    store.clear_object_unresolved_dblinks(engine) # 幂等: 重建前清自己 reason 的 unresolved 行
    dbi = {k.upper(): v for k, v in (dblink_index or {}).items()} if dblink_index is not None else None
    resolver = NameResolver(engine, tables_cfg, dblink_index=dbi)

    edges: list[dict[str, Any]] = []
    evidences: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for dep in store.all_dependencies(engine):
        ref_obj_type = (dep.get("type") or "").upper()
        referenced_type = (dep.get("referenced_type") or "").upper()
        if ref_obj_type not in _REFERENCING_TYPES or referenced_type not in _REFERENCED_TYPES:
            continue
        ref_link = (dep.get("referenced_link_name") or "").upper()
        dst_db_override = ""
        if ref_link:
            if dbi is None:                       # 老行为(dblink_index 未传): skip 跨库依赖
                continue
            # 显式 key-in check: 避免 or-falsy 在 dbi[ref_link]=='' 时错误降级到 base-name 条目。
            # 场景: dbi={'BILLING.WORLD': '', 'BILLING': 'TEST_DB3'}, ref_link='BILLING.WORLD'
            # -> '' 是有意的"不可解"标记, 不能 or-falsy 到 BILLING 的 TEST_DB3。
            base = ref_link.split(".", 1)[0]
            _key = ref_link if ref_link in dbi else (base if base in dbi else None)
            target = dbi[_key] if _key is not None else None
            if not target:                        # 解析不出(key 不存在 or 值为空) -> 登记 unresolved + skip(不产错库边)
                unresolved.append(dict(db_link=ref_link, host="",
                                       reason="object_dep_unresolved",
                                       db_name=dep.get("db_name") or ""))
                continue
            dst_db_override = target
        src_db, src_owner, src_tpl, _src_dt = resolver.resolve_table(
            dep["name"], dep.get("owner") or "", "")
        dst_db, dst_owner, dst_tpl, dst_dt = resolver.resolve_table(
            dep["referenced_name"], dep.get("referenced_owner") or "", "")
        if not src_tpl or not dst_tpl:
            continue
        if (src_owner, src_tpl) == (dst_owner, dst_tpl):   # 自依赖跳过
            continue
        eid = make_edge_id(src_tpl, "", dst_tpl, "", "", src_owner, dst_owner)
        if eid in seen:                            # 同 (src,dst) 多依赖行 -> 一条边
            continue
        seen.add(eid)
        edges.append(dict(
            edge_id=eid, src_db=src_db, src_owner=src_owner, src_table=src_tpl, src_col="",
            dst_db=dst_db_override or dst_db, dst_owner=dst_owner, dst_table=dst_tpl, dst_col="",
            relation_type="", lineage_type="DIRECT",
            # 两侧 dataset_type 对称: 都信 ALL_DEPENDENCIES 自己的类型字段(权威);
            # dst 用 referenced_type(已过滤为 TABLE/VIEW), resolver 的 dst_dt 兜底, 再回落 TABLE。
            src_dataset_type=_normalize_obj_type(ref_obj_type),
            dst_dataset_type=referenced_type or dst_dt or "TABLE",
            confidence="high", evidence_count=1, recovery_mode="", branch_detected=False,
            edge_kind="OBJECT_DEPENDENCY",
            first_seen_at=now, last_seen_at=now, is_active=True,
            source_fingerprint=""))
        evidences.append(dict(
            edge_id=eid, evidence_type="OBJECT_DEPENDENCY", evidence_ref="ALL_DEPENDENCIES",
            excerpt=f"{ref_obj_type} {src_owner}.{src_tpl} -> {dst_owner}.{dst_tpl}",
            extractor_version=EXTRACTOR_VERSION))

    store.write_edges(engine, edges)
    store.write_evidence(engine, evidences)
    if unresolved:
        store.write_unresolved_dblinks(engine, unresolved)
    return dict(dependencies=len(store.all_dependencies(engine)), edges=len(edges),
                evidences=len(evidences), unresolved_dblinks=len(unresolved))


def _normalize_obj_type(ref_type: str) -> str:
    """ALL_DEPENDENCIES.TYPE -> src_dataset_type 展示值。PACKAGE BODY 归 PACKAGE。"""
    return "PACKAGE" if ref_type.startswith("PACKAGE") else ref_type
