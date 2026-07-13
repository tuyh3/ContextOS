"""MyBatis mapper 摄入接线(spec 2026-07-10 §4.6 + 附录 E.5/E.6, L4)。

把 mybatis_extract.expand_mappers 展开的语句落成 lineage 产物:
  (a) 每条语句一行 sql_templates(**含 DML**, container=方法 FQN)——服务 search_sql
      "表 -> 语句 -> 方法" 三跳; 单表 DML 无边但必须留模板(否则整条消失)。
  (b) 多表语句经 parse_sql 出 lineage_edges(复用 pipeline.build_edges_from_relations)。
  (c) FQN 经 code_* 投影校验(E.5): namespace.语句id 命中 code_methods -> 补全带签名 FQN +
      模板 confidence=medium; 未命中/歧义/无投影 -> 裸 FQN + low(弱证据)。
recovery_mode 恒 "mybatis_mapper"(E.6, 已入 RecoveryMode SSOT); 边的表血缘置信 medium
(SQL 已充分展开), 与模板的 FQN 置信是两件事(边讲表关系可靠性, 模板讲方法关联强度)。

边界(动态表名, spec §4.5/§8, 2026-07-11 实测裁决): `CCP_COLL_LOG_${tableTime}` 这类
`${}` 表名插值令 sqlglot parse 失败 -> 落 unresolved 不产边, 也到不了 name_resolve
(故不加家族折叠, 那是零消费者休眠码); 但基表名仍在模板 raw_sql 里, 经 search_sql
icontains 三跳可命中(固定前缀形态)。整名变量 `${queryParams.tableName}` 基表在 Java
call-site 注入, 彻底不可知。数字月表 `_YYYYMM` 能 parse 时由 name_resolve monthly_pattern 折叠。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from contextos.lineage import store
from contextos.lineage.mybatis_extract import expand_mappers
from contextos.lineage.name_resolve import NameResolver
from contextos.lineage.pipeline import build_edges_from_relations
from contextos.lineage.sql_parse import parse_sql

logger = logging.getLogger(__name__)

_MAPPER_RECOVERY = "mybatis_mapper"
_MAPPER_EVIDENCE = "CODE_MYBATIS"


def _resolve_mapper_fqn(conn: Any, fqn: str) -> tuple[str, str, bool]:
    """E.5: 裸 FQN(namespace.语句id)-> code_* 投影校验。

    conn=None(无 code_methods 投影)或空 FQN -> 裸弱证据。命中唯一 -> (带签名 FQN,
    "medium", True); 未命中 / 歧义 -> (裸 FQN, "low", False)(弱证据, 不硬猜)。
    conn 由 ingest_mappers 循环外开一次复用(信创 PG 物理库省 per-语句 connect)。"""
    if conn is None or not fqn:
        return (fqn, "low", False)
    from contextos.code_intel.projection.method_resolve import (
        AmbiguousMethodFqn, resolve_bare_method_fqn,
    )
    try:
        resolved = resolve_bare_method_fqn(conn, fqn)
    except AmbiguousMethodFqn:
        return (fqn, "low", False)    # 多重载歧义: 保守降弱, 不挑一个
    if resolved:
        return (resolved, "medium", True)
    return (fqn, "low", False)


def ingest_mappers(mapper_paths: list[str], *, repo_root: Any, dialect: str,
                   resolver: NameResolver, engine: Engine, now: str = "") -> dict[str, Any]:
    """展开 mapper -> (templates, edges, evidences, unresolved, stats)。build_lineage 合并入 store。

    mapper_paths: 相对 repo_root 的路径(scan_mapper_files 产出)。读取时解析为绝对(不依赖 CWD),
      但 source_path/evidence_ref 存**相对**路径(与 java/sql 候选口径一致)。
    engine: lineage 与 code_* 投影共库(contextos.db), 用于 FQN 校验; 无 code_methods 表
      (如 init --only database)时 FQN 一律降弱(fresh-env 守卫)。dialect 由调用方按
      profile database.type 映射传入(sqlglot 方言 + extract 一致)。"""
    from contextos.lineage.pipeline import _template_id   # 同库私有, 避免重复 id 口径

    repo_root = Path(repo_root)
    abs_paths = [str(repo_root / p) for p in mapper_paths]   # rel/abs-under-root 都能解析
    statements = expand_mappers(abs_paths)
    templates: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    evidences: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen_tpl: set[str] = set()
    fqn_hits = 0
    # FQN 校验连接: code_methods 存在时循环外开一次复用(信创 PG 省 per-语句 connect); 否则 None
    conn = engine.connect() if store.existing_tables(engine, "code_methods") else None
    try:
        for st in statements:
            try:
                src = Path(st.source_path).relative_to(repo_root).as_posix()
            except ValueError:
                src = st.source_path                # 仓外(理论上不会): 存原样
            fqn = f"{st.namespace}.{st.statement_id}" if st.namespace else st.statement_id
            container, confidence, hit = _resolve_mapper_fqn(conn, fqn)
            if hit:
                fqn_hits += 1

            # (a) 模板: 每条语句一行(含 DML), container=FQN
            tid = _template_id(src, container, st.raw_sql)
            if tid not in seen_tpl:
                seen_tpl.add(tid)
                templates.append(dict(
                    template_id=tid, source_file=src, container=container,
                    sql_text=st.raw_sql.strip(), recovery_mode=_MAPPER_RECOVERY,
                    confidence=confidence))

            # (b) 边: parse_sql 多表关系(sqlglot 忽略 -- 分支标记注释; choose 裸表非法 -> error)
            relations, _seq, error = parse_sql(st.raw_sql, dialect=dialect)
            if error:
                unresolved.append(dict(
                    source_path=src, line_start=st.line or 0,
                    recovery_mode=_MAPPER_RECOVERY, reason=error,
                    sql_excerpt=st.raw_sql[:500]))
                continue
            e, ev = build_edges_from_relations(
                relations, resolver, source_path=src, sql_text=st.raw_sql,
                line_start=st.line or 0, confidence="medium", recovery_mode=_MAPPER_RECOVERY,
                now=now, evidence_type=_MAPPER_EVIDENCE)
            edges.extend(e)
            evidences.extend(ev)
    finally:
        if conn is not None:
            conn.close()

    return dict(
        templates=templates, edges=edges, evidences=evidences, unresolved=unresolved,
        stats=dict(mappers=len(mapper_paths), statements=len(statements), fqn_hits=fqn_hits))
