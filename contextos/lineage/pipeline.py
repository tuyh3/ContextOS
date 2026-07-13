"""build pipeline: repo -> 9 层 -> SQLAlchemy 血缘表(移植 LP extract_lineage.py)。

build_lineage(repo_root, code_cfg, tables_cfg, engine) -> stats dict。
全量重建: 先 store.clear_all。branch_detected 候选不产 lineage_edge(§9.3),
只留 evidence + sql_template(供 D10 路径 C 反查)。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from contextos.lineage import store
from contextos.lineage.java_extract import extract_sql_from_java
from contextos.lineage.models import RecoveredSqlCandidate
from contextos.lineage.name_resolve import NameResolver
from contextos.lineage.source_scan import scan_mapper_files, scan_sources
from contextos.lineage.sql_parse import parse_sql
from contextos.lineage.sql_recover import recover_from_sql_file
from contextos.lineage.validate import deduplicate_edges, make_edge_id, validate_edges
from contextos.profile.schema import CodeConfig, TablesConfig

EXTRACTOR_VERSION = "05.1.0"

# audit fix #6: 用单词边界匹配 DML 关键词, 避免列名子串误判
# (如 SELECT UPDATE_TIME, INSERT_USER FROM T_LOG 不应被当 DML 丢弃)。
_DML_KEYWORD_RE = re.compile(r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|MERGE)\b")


def _template_id(source_path: str, container: str, sql_text: str) -> str:
    """按 (source_path, container, sql_text) 取 id: 同文件多 SELECT 各存一条, 同 SQL 抽两次去重
    (Finding #5: 原只 hash source_path 丢同文件第 2 个起的 SELECT, 削弱 D10 路径 C)。"""
    key = f"{source_path}\x00{container}\x00{sql_text.strip()}"
    return "T" + hashlib.md5(key.encode()).hexdigest()[:10].upper()


def build_lineage(repo_root: Path, code_cfg: CodeConfig, tables_cfg: TablesConfig,
                  engine: Engine, now: str = "", *,
                  dblink_index: dict[str, str] | None = None,
                  dialect: str = "oracle", db_type: str = "oracle") -> dict[str, Any]:
    repo_root = Path(repo_root)
    store.create_all(engine)
    store.clear_all(engine)
    resolver = NameResolver(engine, tables_cfg, dblink_index=dblink_index)

    # Layer 2
    sources = scan_sources(repo_root, code_cfg)
    sql_files = [s for s in sources if s.language == "sql"]
    java_files = [s for s in sources if s.language == "java"]

    # Layer 3-4
    candidates: list[RecoveredSqlCandidate] = []
    for sf in sql_files:
        candidates.extend(recover_from_sql_file(sf, dialect=dialect))
    for jf in java_files:
        candidates.extend(extract_sql_from_java(jf.content, jf.path))

    # Layer 5-7-8
    edges: list[dict[str, Any]] = []
    evidences: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    parse_success = parse_fail = 0
    seen_templates: set[str] = set()

    for cand in candidates:
        # 模板: 只读 SELECT/WITH 存 sql_templates(供 D10 路径 C)
        _maybe_template(cand, templates, seen_templates)

        relations, _seq, error = parse_sql(cand.sql_text, dialect=dialect)
        if error:
            parse_fail += 1
            unresolved.append(dict(source_path=cand.source_path, line_start=cand.line_start,
                                   recovery_mode=cand.recovery_mode, reason=error,
                                   sql_excerpt=cand.sql_text[:500]))
            continue
        parse_success += 1

        # §9.3: branch_detected -> 不产 edge(只留 evidence + template)
        if cand.branch_detected:
            continue

        evidence_type = ("CODE_SQL" if cand.recovery_mode in ("sql_file", "semicolon_split")
                         else "CODE_JAVA")
        e, ev = build_edges_from_relations(
            relations, resolver, source_path=cand.source_path, sql_text=cand.sql_text,
            line_start=cand.line_start, confidence=cand.confidence,
            recovery_mode=cand.recovery_mode, now=now, evidence_type=evidence_type)
        edges.extend(e)
        evidences.extend(ev)

    # MyBatis mapper 摄入(spec §4.6/附录 E, L4): 扫 mapper XML(内容识别 + 方言侧选择)->
    # 展开 -> 模板(含 DML)+ 边(多表)+ FQN 校验。db_type 驱动方言目录选择(oracleMapper 对
    # MySQL 目标是漂移死代码)。任何 db_type 都跑一趟全仓 *.xml sniff(build 期一次性, CMPAK
    # 7202 xml ~5.7s, 不在 incremental 路径); 无 mapper 的仓 -> 空清单短路; 有则照收(CMPAK
    # Oracle 实测也有 2 个 mapper)。
    mapper_paths = scan_mapper_files(repo_root, code_cfg, db_type=db_type)
    mres = _ingest_mappers_safe(mapper_paths, repo_root=repo_root, dialect=dialect,
                                resolver=resolver, engine=engine, now=now)
    templates.extend(mres["templates"])
    edges.extend(mres["edges"])
    evidences.extend(mres["evidences"])
    unresolved.extend(mres["unresolved"])

    # Layer 9
    deduped = deduplicate_edges(edges, evidences)
    validated, unknown = validate_edges(deduped, resolver)
    valid_ids = {e["edge_id"] for e in validated}
    evidences = [ev for ev in evidences if ev["edge_id"] in valid_ids]

    # 写 store
    store.write_edges(engine, validated)
    store.write_evidence(engine, evidences)
    store.write_templates(engine, templates)
    store.write_unresolved(engine, unresolved)

    return dict(sql_files=len(sql_files), java_files=len(java_files),
                candidates=len(candidates), parse_success=parse_success,
                parse_fail=parse_fail, edges=len(validated), evidences=len(evidences),
                templates=len(templates), unknown_tables=len(unknown),
                unresolved=len(unresolved),
                mappers=mres["stats"]["mappers"],
                mapper_statements=mres["stats"]["statements"],
                mapper_fqn_hits=mres["stats"]["fqn_hits"])


def _ingest_mappers_safe(mapper_paths: list[str], **kw: Any) -> dict[str, Any]:
    """mapper 摄入包装: 空清单短路(纯 Java 客户零副作用); 惰性 import 破 pipeline<->ingest 环。"""
    empty = dict(templates=[], edges=[], evidences=[], unresolved=[],
                 stats=dict(mappers=0, statements=0, fqn_hits=0))
    if not mapper_paths:
        return empty
    from contextos.lineage.mybatis_ingest import ingest_mappers
    return ingest_mappers(mapper_paths, **kw)


def build_edges_from_relations(
    relations: list[Any], resolver: NameResolver, *, source_path: str, sql_text: str,
    line_start: int, confidence: str, recovery_mode: str, now: str, evidence_type: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """一条候选 SQL 的 relations -> (edges, evidences)。build_lineage 主链与 mapper 摄入共用。

    自连/去重按 (owner, table)(裁决 5 / review HIGH): 显式 schema 的同名表跨 owner 不是自连
    (UPC.COMMON_T vs SEC.COMMON_T); 裸名 owner="" 行为不变。evidence_ref = source_path:line。
    """
    edges: list[dict[str, Any]] = []
    evidences: list[dict[str, Any]] = []
    module_hint = source_path.split("/")[0]
    for rel in relations:
        if rel.src_schema and rel.src_schema.upper() in resolver._exclude:
            continue
        if rel.dst_schema and rel.dst_schema.upper() in resolver._exclude:
            continue
        src_db, src_owner, src_tpl, src_type = resolver.resolve_table(
            rel.src_table, rel.src_schema, module_hint)
        dst_db, dst_owner, dst_tpl, dst_type = resolver.resolve_table(
            rel.dst_table, rel.dst_schema, module_hint)
        if not src_tpl or not dst_tpl or (src_owner, src_tpl) == (dst_owner, dst_tpl):
            continue
        eid = make_edge_id(src_tpl, rel.src_col, dst_tpl, rel.dst_col, rel.relation_type,
                           src_owner, dst_owner)
        edges.append(dict(
            edge_id=eid, src_db=src_db, src_owner=src_owner, src_table=src_tpl,
            src_col=rel.src_col, dst_db=dst_db, dst_owner=dst_owner, dst_table=dst_tpl,
            dst_col=rel.dst_col, relation_type=rel.relation_type,
            lineage_type=rel.lineage_type, src_dataset_type=src_type,
            dst_dataset_type=dst_type, confidence=confidence, evidence_count=1,
            recovery_mode=recovery_mode, branch_detected=False,
            edge_kind="SQL", first_seen_at=now, last_seen_at=now, is_active=True,
            source_fingerprint=hashlib.md5(
                f"{source_path}\x00{sql_text}".encode()).hexdigest()[:16]))
        evidences.append(dict(
            edge_id=eid, evidence_type=evidence_type,
            evidence_ref=f"{source_path}:{line_start}",
            excerpt=sql_text[:200].replace("\n", " "),
            extractor_version=EXTRACTOR_VERSION))
    return edges, evidences


def _maybe_template(cand: RecoveredSqlCandidate, templates: list[dict[str, Any]],
                    seen: set[str]) -> None:
    upper = cand.sql_text.strip().upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return
    if _DML_KEYWORD_RE.search(upper):
        return
    tid = _template_id(cand.source_path, cand.container, cand.sql_text)
    if tid in seen:
        return
    seen.add(tid)
    templates.append(dict(template_id=tid, source_file=cand.source_path,
                          container=cand.container, sql_text=cand.sql_text.strip(),
                          recovery_mode=cand.recovery_mode, confidence=cand.confidence))
