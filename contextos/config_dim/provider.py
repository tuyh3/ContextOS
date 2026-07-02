"""config_dimension_bridge provider(design §12/§13 输出 + §13 corroboration 子分骨架)。

search_config(breakdown, engine) -> ProviderResult(08 §2 统一信封, 复用 provider_io)。

v1 范围(spec §1):输出 **direct_bindings** —— config entity 命中后,候选 signals 只带该
entity 自己最佳 binding 的 bind_target/bind_type/bind_strategy/confidence,**不**展开
transitive 调用链(BFS / callers 是 v2)。table 维候选(candidate_table_terms 命中
config db_table)同形态加,留 C5/pipeline 接缝(本文件先做 file_key/config_key 维)。

§13 corroboration 子分(design §13 SSOT):
  score = 0.30*recall_proxy + 0.25*source_quality + 0.20*business_relevance
        + 0.15*evidence_corroboration + 0.10*rag_corroboration
v1 简化:business_relevance/rag_corroboration 用占位(0.5/0.0),evidence_corroboration
亦占位(0.5);09 校准真信号(05 provider 同款 deferred 接缝模式)。
fail-safe:任何异常 -> ProviderResult.miss(08 §5.1 失败传播)。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Engine

from contextos.config_dim.schema import config_bindings, config_entities, config_sources
from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult

WORKER_NAME = "config_dimension_bridge"

# source_quality by bind_strategy(design §13):越确定的绑定策略权重越高。
_STRAT_Q = {
    "exact_match": 1.0, "annotation_prefix_match": 0.85, "semgrep_rule": 0.8,
    "hierarchical_match": 0.8, "xml_id_match": 0.85, "class_hint_exact": 0.75,
    "class_hint_package": 0.55, "ripgrep_fallback": 0.4, "llm_inferred": 0.3,
    "llm_inferred + rag_corroborated": 0.45, "source_file": 0.4,
}


def _match_entities(breakdown, conn) -> list:
    """candidate_config_keys 子串(大小写不敏感)命中 config_entities.entity_key。

    candidate_config_keys 是 CandidateConfigKey 列表(读 .term 字段;02 需求拆解吐的配置项候选,
    **非裸字符串** —— 跟 sibling lineage/provider.py 一样取 t.term)。此处对每个 entity 做
    `key_term in entity_key` 双向子串中的"term in entity_key"匹配(配置 key 一般比候选 term 长,
    如 candidate 'offer.switch' 命中 entity 'offer.switch.enable')。
    """
    terms = [t.term.strip() for t in (getattr(breakdown, "candidate_config_keys", []) or [])
             if t.term.strip()]
    if not terms:
        return []
    lowered = [t.lower() for t in terms]
    ents = conn.execute(select(config_entities)).fetchall()
    return [e for e in ents if any(t in e.entity_key.lower() for t in lowered)]


def _match_tables(breakdown, conn) -> list:
    """candidate_table_terms 子串(大小写不敏感)命中 config_sources(db_table).table_name。

    返回命中的 config_sources 行(db_table 类型),供 table 维候选用。owner 在调用方直接取
    s.owner(path B 在 pipeline.py:174 已设),**不** JOIN/全表扫 owner_resolution —— 后者是
    edge-keyed overlay 给 lineage(trace_config_impact, Plan 10)用,串到无关表会污染(W3 HIGH 2)。
    """
    terms = [t.term.strip() for t in (getattr(breakdown, "candidate_table_terms", []) or [])
             if t.term.strip()]
    if not terms:
        return []
    lowered = [t.lower() for t in terms]
    srcs = conn.execute(
        select(config_sources).where(config_sources.c.source_type == "db_table")).fetchall()
    return [s for s in srcs if any(t in (s.table_name or "").lower() for t in lowered)]


def search_config(breakdown, engine: Engine) -> ProviderResult:
    try:
        with engine.connect() as conn:
            ents = _match_entities(breakdown, conn)
            srcs = _match_tables(breakdown, conn)
            if not ents and not srcs:
                return ProviderResult.miss(WORKER_NAME, "no_entity_match")

            # entity_id -> 该 entity 的所有 binding(direct, 不递归 caller 链)
            binds_by_ent: dict[str, list] = {}
            for b in conn.execute(select(config_bindings)).fetchall():
                binds_by_ent.setdefault(b.entity_id, []).append(b)

            candidates: list[ProviderCandidate] = []
            quals: list[float] = []
            for e in ents:
                bs = binds_by_ent.get(e.entity_id, [])
                best = max(bs, key=lambda b: _STRAT_Q.get(b.bind_strategy, 0.3), default=None)
                sig: dict = {"entity_key": e.entity_key, "entity_type": e.entity_type}
                if best is not None:
                    sig.update(bind_type=best.bind_type, bind_target=best.bind_target,
                               bind_strategy=best.bind_strategy, confidence=best.confidence)
                    quals.append(_STRAT_Q.get(best.bind_strategy, 0.3))
                candidates.append(ProviderCandidate(target=e.entity_key, kind="CONFIG_KEY", signals=sig))

            # table 维候选:candidate_table_terms 命中 config_sources(db_table)。owner 直接取
            # config_sources.owner(path B 已写, pipeline.py:174);空则留裸表名,**不**全表扫
            # owner_resolution overlay(edge-keyed 给 lineage, 串到无关表会污染, W3 HIGH 2)。
            for s in srcs:
                ow = s.owner or ""
                target = f"{ow}.{s.table_name}" if ow else s.table_name
                # kind=CONFIG_TABLE(配置维枚举 KIND_CONFIG_DIMENSION):本桥是配置维 provider,
                # 表维候选是"被识别为配置表的 DB 表",不是 05 血缘维的 SQL_TABLE。见 01 §3.1 +
                # enums.KIND_CONFIG_DIMENSION + 本 design §0 banner kind SSOT 修正。
                candidates.append(ProviderCandidate(
                    target=target, kind="CONFIG_TABLE",
                    signals={"table": s.table_name, "resolved_owner": ow, "db": s.db_name}))
                # source_quality:db_table 命中按中档(owner-resolved -> 略高, 裸名 -> 中)。
                quals.append(0.6 if ow else 0.4)

            n_keys = len(getattr(breakdown, "candidate_config_keys", []) or [])
            n_tables = len(getattr(breakdown, "candidate_table_terms", []) or [])
            expected = max(n_keys + n_tables, 1)  # 分母含 config_key + table 两维候选
            recall_proxy = min((len(ents) + len(srcs)) / expected, 1.0)
            source_quality = sum(quals) / len(quals) if quals else 0.3
            # business_relevance / rag_corroboration / evidence_corroboration:
            # v1 占位(0.5 / 0.0 / 0.5),09 用真 RAG 印证 + binding 多证据校准。
            score = round(0.30 * recall_proxy + 0.25 * source_quality
                          + 0.20 * 0.5 + 0.15 * 0.5 + 0.10 * 0.0, 4)
            return ProviderResult(
                worker_name=WORKER_NAME, score=min(score, 1.0),
                score_breakdown={"recall_proxy": round(recall_proxy, 4),
                                 "source_quality": round(source_quality, 4),
                                 "business_relevance": 0.5, "evidence_corroboration": 0.5,
                                 "rag_corroboration": 0.0, "rag_deferred": 1.0,
                                 "matched_entities": float(len(ents)),
                                 "matched_tables": float(len(srcs))},
                candidates=candidates,
                reasoning=f"{len(ents)} config entity + {len(srcs)} config table matched "
                          f"(direct_bindings); business_relevance/rag_corroboration deferred "
                          f"(09 calibration)",
                miss_reason=None)
    except Exception as exc:  # fail-safe -> miss(08 §5.1)
        return ProviderResult.miss(WORKER_NAME, f"provider_error:{type(exc).__name__}")
