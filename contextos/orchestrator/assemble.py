# contextos/orchestrator/assemble.py
"""把 corroboration 结果组装成 01 Impact Map(EvidenceItemWithDimensions + 顶层 ImpactMap)。

重建维度扩展(SqlLineage/ConfigBinding)from candidate signals,含 G1/G2/G3 兜底。
敏感值脱敏:value_raw / 配置原始值 / 表数据快照绝不进输出。
"""
from __future__ import annotations

from typing import cast, get_args

from contextos.impact_map.dimensions import ConfigBinding, SqlLineage, TableRef
from contextos.impact_map.enums import (
    KIND_CONFIG_DIMENSION,
    KIND_SQL_DIMENSION,
    BindStrategy,
    BindType,
    ChangeType,
    ConfidenceTier,
    DimensionKey,
    DimensionQuality,
    DimensionStatus,
    EntityType,
    EntrypointKind,
    Kind,
    LineageType,
    RecoveryMode,
    RelationType,
    SourceType,
)
from contextos.impact_map.evidence import EvidenceRef
from contextos.impact_map.schema import (
    EntrypointRef,
    EvidenceItemWithDimensions,
    ImpactMap,
    MatchedCapability,
)
from contextos.orchestrator.change_type import infer_change_type
from contextos.orchestrator.corroboration import CorroboratedCandidate
from contextos.orchestrator.provider_io import _safe_int

# worker_name -> 01 evidence_refs.source(KNOWN_EVIDENCE_SOURCES 已注册)。
# code/llm/rag 固定;05/06 按 recovery_mode / raw bind_strategy 派生真实 provenance(review MEDIUM 4)。
_FIXED_SOURCE = {
    "code_search": "jdt-ls-workspaceSymbol",
    "llm_rerank": "llm-rerank",
    "rag": "rag-cross-encoder",
}
_DB_SOURCE_BY_RECOVERY = {
    "literal": "lp-sql-recover-literal",
    "sql_file": "lp-sql-recover-literal",
    "concat": "lp-java-extract-concat",
    "string_builder": "lp-java-extract-builder",
    "mybatis_mapper": "lp-mybatis-mapper2sql",   # 多方言 spec E.6: mapper 摄入边真实 provenance
}
_CONFIG_SOURCE_BY_STRATEGY = {
    "exact_match": "lp-bind-resolver-exact",
    "annotation_prefix_match": "lp-bind-resolver-prefix",
    "hierarchical_match": "lp-bind-resolver-prefix",
    "xml_id_match": "lp-bind-resolver-prefix",
    "class_hint_exact": "lp-bind-resolver-prefix",
    "class_hint_package": "lp-bind-resolver-prefix",
    "semgrep_rule": "lp-bind-resolver-semgrep-rule",
    "ripgrep_fallback": "ripgrep-config-fallback",
    "source_file": "ripgrep-config-fallback",
}

# G2:06 bind_strategy 词表(_STRAT_Q 11 值)超出 01 BindStrategy(5 值)枚举 -> 映射。
# 原值留 metadata["raw_bind_strategy"];score_bridge 仍用原值查 Q_bind(不受映射影响)。
_VALID_BIND_STRATEGY = frozenset(get_args(BindStrategy))
_BIND_STRATEGY_ALIAS = {
    "hierarchical_match": "annotation_prefix_match",
    "xml_id_match": "annotation_prefix_match",
    "class_hint_exact": "annotation_prefix_match",
    "class_hint_package": "annotation_prefix_match",
    "source_file": "ripgrep_fallback",
    "llm_inferred + rag_corroborated": "llm_inferred",
}
_VALID_RELATION = frozenset(get_args(RelationType))
_VALID_LINEAGE = frozenset(get_args(LineageType))
_VALID_RECOVERY = frozenset(get_args(RecoveryMode))
_VALID_ENTITY = frozenset(get_args(EntityType))
_VALID_BIND_TYPE = frozenset(get_args(BindType))
_VALID_KIND = frozenset(get_args(Kind))

# 06 signals 无 source_type -> 从 entity_type 派生
_SOURCE_TYPE_FROM_ENTITY = {"file_key": "file", "db_table": "db_table", "db_key_pattern": "db_table"}

_METHOD_KINDS = frozenset({"METHOD", "CLASS", "INTERFACE", "FIELD",
                           "API_ENTRY", "JOB", "BATCH", "MSG"})
_ENTRYPOINT_KIND_MAP = {"API_ENTRY": "API", "JOB": "JOB", "BATCH": "BATCH", "MSG": "MSG"}


def _map_bind_strategy(raw: str | None) -> str:
    if raw in _VALID_BIND_STRATEGY:
        return raw  # type: ignore[return-value]
    return _BIND_STRATEGY_ALIAS.get(raw or "", "ripgrep_fallback")


def _table_ref(d: object) -> TableRef | None:
    """从 signals 的 src/dst dict 稳健建 TableRef:只取已知字段(防 _StrictBase extra=forbid 崩),
    缺字段给默认(防 Field required 崩)。非 dict / 无 table -> None。fail-safe §5.1 + 与 dst 对称。"""
    if not isinstance(d, dict) or not d.get("table"):
        return None
    col = d.get("col")
    return TableRef(db=str(d.get("db") or ""), owner=str(d.get("owner") or ""),
                    table=str(d.get("table")), col=str(col) if col is not None else None)


def _build_sql_lineage(sig: dict) -> SqlLineage:
    rel = sig.get("relation_type")
    lin = sig.get("lineage_type")
    mode = sig.get("recovery_mode")
    return SqlLineage(
        relation_type=cast(RelationType, rel if rel in _VALID_RELATION else "WHERE_EQ"),  # G3
        lineage_type=cast(LineageType, lin if lin in _VALID_LINEAGE else "INDIRECT"),      # G3(空/D10 -> INDIRECT)
        src=_table_ref(sig.get("src")),                                   # 稳健建(防 partial/extra-key 崩, 与 dst 对称)
        dst=_table_ref(sig.get("dst")) or TableRef(db="", owner="", table="UNKNOWN", col=None),
        evidence_count=max(1, _safe_int(sig.get("evidence_count", 0))),   # G1 floor + 坏类型 -> 0(fail-safe §5.1)
        sql_template_id=sig.get("sql_template_id"),
        # 未知 recovery_mode -> literal(与 corroboration._score_db 对称容错, 防 Literal ValidationError; 05 列是无约束 String)
        recovery_mode=cast(RecoveryMode, mode if mode in _VALID_RECOVERY else "literal"),
        branch_detected=bool(sig.get("branch_detected", False)),
        unresolved_reason=sig.get("unresolved_reason"))


def _build_config_binding(kind: str, sig: dict) -> ConfigBinding:
    if kind == "CONFIG_TABLE":
        return ConfigBinding(
            entity_type="db_table", source_type="db_table",
            bind_type="table", bind_direction="both",
            bind_strategy="exact_match", is_sensitive=False)
    et = str(sig.get("entity_type") or "file_key")
    if et not in _VALID_ENTITY:        # 未知 entity_type -> file_key(防 Literal ValidationError; 06 列无约束 String)
        et = "file_key"
    bt = str(sig.get("bind_type") or "domain")
    if bt not in _VALID_BIND_TYPE:     # 未知 bind_type -> domain(同上, 与 G2 bind_strategy 对称)
        bt = "domain"
    return ConfigBinding(
        entity_type=cast(EntityType, et),
        source_type=cast(SourceType, _SOURCE_TYPE_FROM_ENTITY.get(et, "file")),
        bind_type=cast(BindType, bt),
        bind_direction="read",                                           # 06 signals 无 direction -> read
        bind_strategy=cast(BindStrategy, _map_bind_strategy(sig.get("bind_strategy"))),
        is_sensitive=False)                                              # 敏感值脱敏:value_raw 留空


def _evidence_source(worker: str, cc: CorroboratedCandidate) -> str:
    """worker -> 01 source,按 recovery_mode / bind_strategy 派生真实 provenance(review MEDIUM 4)。"""
    if worker in _FIXED_SOURCE:
        return _FIXED_SOURCE[worker]
    if worker == "db_lineage_bridge":
        mode = str((cc.signals_by_worker.get("db_lineage_bridge") or {}).get("recovery_mode") or "")
        return _DB_SOURCE_BY_RECOVERY.get(mode, "lp-sqlglot-parse")     # 其它 mode -> 通用 parse
    if worker == "config_dimension_bridge":
        if cc.kind == "CONFIG_TABLE":
            return "lp-db-config-marker"
        strat = str((cc.signals_by_worker.get("config_dimension_bridge") or {}).get("bind_strategy") or "")
        return _CONFIG_SOURCE_BY_STRATEGY.get(strat, "ripgrep-config-fallback")  # 未知 -> 保守 fallback
    return worker


def _evidence_refs(cc: CorroboratedCandidate) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for w in cc.hit_workers:
        score = cc.rag_score if w == "rag" else cc.bridge_scores.get(w, 0.0)
        refs.append(EvidenceRef(source=_evidence_source(w, cc),
                                rerank_score=max(0.0, min(1.0, score))))
    if not refs:                                                         # EvidenceItem min_length=1 兜底
        refs.append(EvidenceRef(source="human-annotation", rerank_score=0.0))
    return refs


def to_evidence_item(cc: CorroboratedCandidate, ev_id: str,
                     actions: list[str]) -> EvidenceItemWithDimensions:
    sql_lineage = None
    config_binding = None
    raw_strat = None
    if cc.kind in KIND_SQL_DIMENSION:
        sql_lineage = _build_sql_lineage(cc.signals_by_worker.get("db_lineage_bridge") or {})
    elif cc.kind in KIND_CONFIG_DIMENSION:
        cfg_sig = cc.signals_by_worker.get("config_dimension_bridge") or {}
        raw_strat = cfg_sig.get("bind_strategy")
        config_binding = _build_config_binding(cc.kind, cfg_sig)

    code_sig = cc.signals_by_worker.get("code_search") or {}
    # fail-safe(§5.1): eligible_bridges 对未知 kind 兜底 {llm_rerank} 让 corroboration 不空分母,
    # 但 01 EvidenceItem.kind 是闭 Literal -> 未知/空 kind 归一到 OTHER(原值留 metadata.raw_kind),
    # 否则 assembly 阶段 ValidationError 崩整轮(ProviderCandidate.kind 是开放 str, 未来 provider /
    # Plan 10 MCP host 可能吐非枚举 kind; eligible_bridges 已声明容忍, 此处补齐至输出边界)。
    kind = cc.kind if cc.kind in _VALID_KIND else "OTHER"
    meta: dict = {"folded": cc.folded, "consensus_count": cc.consensus_count,
                  "bridge_scores": cc.bridge_scores}
    if raw_strat is not None:
        meta["raw_bind_strategy"] = raw_strat
    if kind != cc.kind:
        meta["raw_kind"] = cc.kind
    # 04b freshness 三键(spec §9): code_sig 带非空 freshness 才写 code_projection
    # (live-JDT 路径无此三键 / 全空串 -> 不出现该 metadata 键)。
    proj = {k: code_sig[k] for k in
            ("projection_build_id", "indexed_commit", "projection_status")
            if code_sig.get(k)}
    if proj:
        meta["code_projection"] = proj
    # Block 1a Finding #2: 对象依赖候选(kind=OBJECT_DEPENDENCY)归 OTHER, 补轻量结构化详情进
    # metadata(不建 sql_lineage / 不污染 SQL 表维度)。详情从 db_lineage_bridge signal 取。
    od = (cc.signals_by_worker.get("db_lineage_bridge") or {}).get("object_dependency")
    if cc.kind == "OBJECT_DEPENDENCY" and isinstance(od, dict):
        meta["object_dependency"] = {
            "dep_type": str(od.get("dep_type") or ""),
            "src_object": str(od.get("src_object") or ""),
            "dst_table": str(od.get("dst_table") or ""),
            "evidence_ref": str(od.get("evidence_ref") or "ALL_DEPENDENCIES"),
        }

    return EvidenceItemWithDimensions(
        id=ev_id, target=cc.target, kind=cast(Kind, kind),
        file=code_sig.get("file"),
        line_start=code_sig.get("line_start"),
        line_end=code_sig.get("line_end"),
        change_type=cast(ChangeType, infer_change_type(cc.kind, actions)),
        confidence=max(0.0, min(1.0, cc.score_overall)),                 # clamp 与 evidence_refs rerank_score 对称(防 [0,1] 越界 ValidationError)
        confidence_tier=cast(ConfidenceTier, cc.confidence_tier),
        evidence_refs=_evidence_refs(cc),
        reasoning=f"corroborated by {','.join(cc.hit_workers)} "
                  f"(tier={cc.confidence_tier}, consensus={cc.consensus_count})",
        sql_lineage=sql_lineage, config_binding=config_binding, metadata=meta)


def _dimension_status(corrobs: list[CorroboratedCandidate]) -> dict[DimensionKey, DimensionStatus]:
    kinds = {cc.kind for cc in corrobs}
    status: dict[DimensionKey, DimensionStatus] = {
        "method": "resolved" if kinds & _METHOD_KINDS else "not_applicable",
        "sql_table": "resolved" if kinds & set(KIND_SQL_DIMENSION) else "not_applicable",
        "config": "resolved" if kinds & set(KIND_CONFIG_DIMENSION) else "not_applicable",
    }
    return status


# 质量轴判定(spec 2026-06-17 §5.3)。domain 定位桥 / 各维兜底源集。
_DOMAIN_WORKER = {
    "method": "code_search",
    "sql_table": "db_lineage_bridge",
    "config": "config_dimension_bridge",
}
# 兜底定位源集(各维不同; SSOT = _evidence_source 对该 domain worker 的兜底返回)。
# config: ripgrep 兜底 = grep 命中非真绑定。SQL/method 空集 -> 永不 fallback_only。
_FALLBACK_SOURCES = {
    "method": frozenset(),
    "sql_table": frozenset(),
    "config": frozenset({"ripgrep-config-fallback"}),
}


def _dimension_of(kind: str) -> DimensionKey | None:
    if kind in _METHOD_KINDS:
        return "method"
    if kind in KIND_SQL_DIMENSION:
        return "sql_table"
    if kind in KIND_CONFIG_DIMENSION:
        return "config"
    return None   # OTHER / v2 占位 -> 不进质量轴


def _quality_for_dimension(dim: DimensionKey, ccs: list[CorroboratedCandidate],
                           consensus_min_bridges: int) -> DimensionQuality:
    if not ccs:
        return "not_applicable"
    fb = _FALLBACK_SOURCES[dim]
    if fb:
        worker = _DOMAIN_WORKER[dim]
        # 按 domain 桥 source 判(排除 llm-rerank/rag 这类非定位 ref): 全兜底 -> fallback_only
        all_fallback = True
        for cc in ccs:
            src = _evidence_source(worker, cc) if worker in cc.hit_workers else None
            if src not in fb:
                all_fallback = False
                break
        if all_fallback:
            return "fallback_only"
    has_strong = any(
        cc.confidence_tier == "HIGH" or cc.consensus_count >= consensus_min_bridges
        for cc in ccs
    )
    return "strong" if has_strong else "low_confidence"


def _dimension_quality(corrobs: list[CorroboratedCandidate],
                       consensus_min_bridges: int) -> dict[DimensionKey, DimensionQuality]:
    by_dim: dict[DimensionKey, list[CorroboratedCandidate]] = {
        "method": [], "sql_table": [], "config": []}
    for cc in corrobs:
        dim = _dimension_of(cc.kind)
        if dim is not None:
            by_dim[dim].append(cc)
    return {dim: _quality_for_dimension(dim, ccs, consensus_min_bridges)
            for dim, ccs in by_dim.items()}


def assemble_impact_map(breakdown, corrobs: list[CorroboratedCandidate],
                        consensus_min_bridges: int = 2) -> ImpactMap:
    # evidence_items = 全召回:folded 候选也放进来(metadata.folded=True),消费方按需过滤(review HIGH 2;
    # 07 §3「recall 一条不丢」/ 08 §3.2「LOW 默认折叠不展示, 数据保留」)。
    actions = list(breakdown.actions)
    items = [to_evidence_item(cc, f"ev{i:04d}", actions) for i, cc in enumerate(corrobs)]
    entrypoints = [EntrypointRef(kind=cast(EntrypointKind, _ENTRYPOINT_KIND_MAP[cc.kind]), target=cc.target)
                   for cc in corrobs if cc.kind in _ENTRYPOINT_KIND_MAP]
    caps = [MatchedCapability(capability=m.capability, confidence=m.confidence)
            for m in breakdown.matched_capabilities]
    summary = (breakdown.business_intent or breakdown.raw_text[:200]).strip() or "(no summary)"
    return ImpactMap(
        requirement_id=breakdown.requirement_id,
        requirement_summary=summary,
        matched_business_capabilities=caps,
        candidate_entrypoints=entrypoints,
        dimension_status=_dimension_status(corrobs),
        dimension_quality=_dimension_quality(corrobs, consensus_min_bridges),
        evidence_items=items,
        open_questions=list(breakdown.open_questions),
        metadata={"assessment": breakdown.assessment,
                  "requirement_confidence": breakdown.confidence})
