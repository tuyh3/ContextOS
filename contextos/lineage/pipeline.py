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
from contextos.lineage.source_scan import scan_sources
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
                  dblink_index: dict[str, str] | None = None) -> dict[str, Any]:
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
        candidates.extend(recover_from_sql_file(sf))
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

        relations, _seq, error = parse_sql(cand.sql_text)
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

        for rel in relations:
            if rel.src_schema and rel.src_schema.upper() in resolver._exclude:
                continue
            if rel.dst_schema and rel.dst_schema.upper() in resolver._exclude:
                continue
            module_hint = cand.source_path.split("/")[0]
            src_db, src_owner, src_tpl, src_type = resolver.resolve_table(
                rel.src_table, rel.src_schema, module_hint)
            dst_db, dst_owner, dst_tpl, dst_type = resolver.resolve_table(
                rel.dst_table, rel.dst_schema, module_hint)
            # 自连/去重按 (owner, table)(裁决 5 / review HIGH): 显式 schema 的同名表
            # 跨 owner 不是自连(UPC.COMMON_T vs SEC.COMMON_T); 裸名 owner="" 行为不变。
            if not src_tpl or not dst_tpl or (src_owner, src_tpl) == (dst_owner, dst_tpl):
                continue
            eid = make_edge_id(src_tpl, rel.src_col, dst_tpl, rel.dst_col, rel.relation_type,
                               src_owner, dst_owner)
            edges.append(dict(
                edge_id=eid, src_db=src_db, src_owner=src_owner, src_table=src_tpl,
                src_col=rel.src_col, dst_db=dst_db, dst_owner=dst_owner, dst_table=dst_tpl,
                dst_col=rel.dst_col, relation_type=rel.relation_type,
                lineage_type=rel.lineage_type, src_dataset_type=src_type,
                dst_dataset_type=dst_type, confidence=cand.confidence, evidence_count=1,
                recovery_mode=cand.recovery_mode, branch_detected=cand.branch_detected,
                edge_kind="SQL", first_seen_at=now, last_seen_at=now, is_active=True,
                source_fingerprint=hashlib.md5(
                    f"{cand.source_path}\x00{cand.sql_text}".encode()).hexdigest()[:16]))
            evidences.append(dict(
                edge_id=eid,
                evidence_type="CODE_SQL" if cand.recovery_mode in ("sql_file", "semicolon_split")
                else "CODE_JAVA",
                evidence_ref=f"{cand.source_path}:{cand.line_start}",
                excerpt=cand.sql_text[:200].replace("\n", " "),
                extractor_version=EXTRACTOR_VERSION))

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
                unresolved=len(unresolved))


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
