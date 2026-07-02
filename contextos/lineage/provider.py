"""db_lineage_bridge provider(§13 输出 + §14 corroboration 子分)。

search_lineage(breakdown, engine, *, method_source_paths=None) -> ProviderResult。
v1 降级: business_relevance 仅在 Oracle comment 存在时 >0; rag_corroboration=0(03b 未 merge)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

from contextos.lineage import store
from contextos.lineage.dataflow import trace_method_dataflow
from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.requirement.schema import RequirementBreakdown

WORKER_NAME = "db_lineage_bridge"

# §14 source_quality: recovery_mode -> 权重
_RECOVERY_WEIGHT = {
    "literal": 1.0, "sql_file": 1.0, "local_var": 0.8, "static_const": 0.7,
    "concat": 0.7, "string_builder": 0.4, "semicolon_split": 0.3,
}


def _canonical(db: str, owner: str, table: str) -> str:
    parts = [p for p in (db, owner, table) if p]
    return ".".join(parts) if parts else table


def _is_base_table(dataset_type: str | None) -> bool:
    """匹配侧是不是『真实基表』。空/未知 -> 当基表(向后兼容静态 SQL 边 + store 默认 TABLE)。
    只有明确的非表对象(VIEW/PROCEDURE/TRIGGER/...)才不算基表 -> 才进对象依赖维度。"""
    return (dataset_type or "TABLE").upper() in ("", "TABLE")


def _other_side(edge: dict[str, Any], side: dict[str, Any]) -> tuple[str, str, str]:
    """对象依赖边的『另一侧』(命中对象所依赖/被依赖的表)的 (db, owner, table)。
    side 是匹配侧标识(由遍历时填 which="src"/"dst")。"""
    if side.get("which") == "src":
        return edge["dst_db"], edge["dst_owner"], edge["dst_table"]
    return edge["src_db"], edge["src_owner"], edge["src_table"]


def search_lineage(breakdown: RequirementBreakdown, engine: Engine, *,
                   method_source_paths: list[str] | None = None) -> ProviderResult:
    if breakdown.assessment == "rejected":
        return ProviderResult.miss(WORKER_NAME, "requirement_rejected")

    terms = [t.term.strip() for t in breakdown.candidate_table_terms if t.term.strip()]
    if not terms and not method_source_paths:
        return ProviderResult.miss(WORKER_NAME, "no_table_terms")

    edges = store.all_edges(engine)
    # 裁决 5: 同名表可跨 owner -> 裸名映射到多条元数据行(Finding #1: 不再 dict 静默挑最后一条)。
    md_rows: dict[str, list[dict[str, Any]]] = {}
    for _r in store.all_table_metadata(engine):
        md_rows.setdefault(_r["template_name"], []).append(_r)

    # term -> 匹配的边(src/dst 表名子串, 大小写不敏感)
    # 身份键 = (candidate_kind, table.upper()), 不是裸表名(probe important 回归):
    # 同一个名字(典型: 视图)可同时是被 Java SQL 查询的表(SQL 边 -> SQL_TABLE 候选)
    # 与 ALL_DEPENDENCIES 里的非表对象(OBJECT_DEPENDENCY 边 -> OBJECT_DEPENDENCY 候选)。
    # 裸名去重 + first-write-wins 会按 build 行顺序静默丢掉对象依赖维度(SQL 边先写故先赢),
    # 令 Impact Map 内容随行顺序变化(非确定性)。按 (kind, table) 分桶让两维度并存, 且与最终
    # corroboration 的 (kind, target) 身份键对齐(那一层本就不碰撞)。
    matched: dict[tuple[str, str], dict[str, Any]] = {}   # (kind, table) -> representative candidate
    upper_terms = [t.upper() for t in terms]

    def _consider(table: str, side: dict[str, Any], edge: dict[str, Any]) -> None:
        if not table:
            return
        edge_kind = edge.get("edge_kind") or "SQL"
        # Finding #2(quality important): 分流按**匹配侧的 dataset_type**, 不是只看 edge_kind。
        # 对象依赖边 = src(VIEW/PROC) -> dst(真实 TABLE)。term 命中 dst 这张真表时, 它是合法
        # 基表, 必须留 SQL_TABLE(否则 assemble 归 OTHER, 真表掉出 sql_table 维度); 只有命中
        # src 这种非表对象才走 OBJECT_DEPENDENCY。side["dataset_type"] = 匹配侧自己的类型。
        is_object_dep = edge_kind == "OBJECT_DEPENDENCY" and not _is_base_table(side.get("dataset_type"))
        cand_kind = "OBJECT_DEPENDENCY" if is_object_dep else "SQL_TABLE"
        key = (cand_kind, table.upper())
        prev = matched.get(key)
        if prev is None:
            cand = dict(table=table, db=side["db"], owner=side["owner"],
                        relation_type=edge["relation_type"],
                        lineage_type=edge["lineage_type"],
                        recovery_mode=edge["recovery_mode"],
                        branch_detected=edge["branch_detected"],
                        evidence_count=edge.get("evidence_count", 0),
                        kind=cand_kind,
                        src=dict(db=edge["src_db"], owner=edge["src_owner"],
                                 table=edge["src_table"], col=edge["src_col"] or None),
                        dst=dict(db=edge["dst_db"], owner=edge["dst_owner"],
                                 table=edge["dst_table"], col=edge["dst_col"] or None))
            if is_object_dep:
                # dep_type = 匹配侧(命中的那个对象)自己的 dataset_type, 不是固定取 src 那侧;
                # src_object = 命中的对象, dst_table = 它依赖的表(边的另一侧)。
                cand["object_dependency"] = dict(
                    dep_type=(side.get("dataset_type") or "").upper(),
                    src_object=_canonical(side["db"], side["owner"], table),
                    dst_table=_canonical(*_other_side(edge, side)),
                    evidence_ref="ALL_DEPENDENCIES")
            matched[key] = cand
        else:
            prev["evidence_count"] += edge.get("evidence_count", 0)

    for edge in edges:
        src_t, dst_t = edge["src_table"] or "", edge["dst_table"] or ""
        # which/dataset_type 透传匹配侧自身的类型, 供 _consider 按匹配侧分流(Finding #2)。
        for tbl, side_db, side_owner, which, ds_type in (
                (src_t, edge["src_db"], edge["src_owner"], "src", edge.get("src_dataset_type")),
                (dst_t, edge["dst_db"], edge["dst_owner"], "dst", edge.get("dst_dataset_type"))):
            if not tbl:
                continue
            tu = tbl.upper()
            if any(t in tu or tu in t for t in upper_terms):
                _consider(tbl, {"db": side_db, "owner": side_owner,
                                "which": which, "dataset_type": ds_type}, edge)

    # D10: 04 给的方法所在文件 -> 补表(D10 命中均为 SQL_TABLE 维度; 键随 matched 改 (kind, table))
    if method_source_paths:
        for sp in method_source_paths:
            for hit in trace_method_dataflow(engine, source_path=sp):
                tbl = hit["table"]
                d10_key = ("SQL_TABLE", tbl.upper())
                if d10_key not in matched:
                    matched[d10_key] = dict(
                        table=tbl, db="", owner="", relation_type=hit["relation_type"],
                        lineage_type="", recovery_mode="sql_file", branch_detected=False,
                        evidence_count=1, kind="SQL_TABLE",
                        src=dict(db="", owner="", table="", col=None),
                        dst=dict(db="", owner="", table=tbl, col=None))

    if not matched:
        return ProviderResult.miss(WORKER_NAME, "no_table_match")

    candidates: list[ProviderCandidate] = []
    for _key, m in matched.items():
        # target 用边自己已解析的 owner/db(resolve_table 已尊重显式 schema + 单 owner 富化);
        # 不借 md_rows 的 owner, 否则边 SEC.T_X 会被 metadata 的 UPC.T_X 错盖(review 三轮 HIGH 同类)。
        target = _canonical(m["db"], m["owner"], m["table"])
        if m.get("kind") == "OBJECT_DEPENDENCY":
            # Finding #2: 对象依赖边走独立 kind(不污染 SQL 表维度), 不带 relation/src/dst SQL 信号;
            # 详情走 object_dependency 子对象(assemble 挂进 metadata, 08 corroboration 归 OTHER)。
            candidates.append(ProviderCandidate(
                target=target, kind="OBJECT_DEPENDENCY",
                signals=dict(object_dependency=m["object_dependency"],
                             evidence_count=m["evidence_count"])))
        else:
            candidates.append(ProviderCandidate(
                target=target, kind="SQL_TABLE",
                signals=dict(relation_type=m["relation_type"], lineage_type=m["lineage_type"],
                             src=m["src"], dst=m["dst"], evidence_count=m["evidence_count"],
                             sql_template_id=None, recovery_mode=m["recovery_mode"],
                             branch_detected=m["branch_detected"], unresolved_reason=None)))

    score, breakdown_dict = _score(matched, terms, md_rows)
    return ProviderResult(worker_name=WORKER_NAME, score=score,
                          score_breakdown=breakdown_dict, candidates=candidates,
                          reasoning=f"matched {len(candidates)} table(s) for {len(terms)} term(s)"
                                    f"{' + D10 method dataflow' if method_source_paths else ''}; "
                                    f"RAG business-relevance deferred (03b not merged)",
                          miss_reason=None)


def _score(matched: dict[tuple[str, str], dict[str, Any]], terms: list[str],
           md_rows: dict[str, list[dict[str, Any]]]) -> tuple[float, dict[str, float]]:
    n = len(matched)
    recall_proxy = min(1.0, n / max(1, len(terms))) if terms else (1.0 if n else 0.0)
    source_quality = (sum(_RECOVERY_WEIGHT.get(m["recovery_mode"], 0.4)
                          - (0.2 if m["branch_detected"] else 0.0)
                          for m in matched.values()) / n) if n else 0.0
    # 裁决 5: 任一 owner 的同名表有 comment 即算业务相关(Finding #1: 不只看静默挑的那条)。
    # 键现为 (kind, table); md_rows 按裸表名 -> 取 key[1] 查 comment(probe important 修复连带点)。
    business_relevance = (sum(1 for (_kind, tbl) in matched
                              if any((row.get("comment") or "").strip()
                                     for row in md_rows.get(tbl, [])))
                          / n) if n else 0.0
    evidence_corroboration = (sum(1 for m in matched.values() if m["evidence_count"] >= 2)
                              / n) if n else 0.0
    rag_corroboration = 0.0  # 03b 未 merge, 留接缝
    score = (0.30 * recall_proxy + 0.25 * source_quality + 0.20 * business_relevance
             + 0.15 * evidence_corroboration + 0.10 * rag_corroboration)
    high_conf = sum(1 for m in matched.values()
                    if _RECOVERY_WEIGHT.get(m["recovery_mode"], 0.4) >= 0.7) / n if n else 0.0
    breakdown = dict(
        candidate_count=float(n), recall_proxy=round(recall_proxy, 4),
        source_quality=round(source_quality, 4),
        business_relevance=round(business_relevance, 4),
        evidence_corroboration=round(evidence_corroboration, 4),
        rag_corroboration=0.0, rag_deferred=1.0,
        high_confidence_ratio=round(high_conf, 4),
        unknown_tables_count=0.0)
    return round(min(1.0, score), 4), breakdown
