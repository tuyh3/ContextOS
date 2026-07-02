# contextos/orchestrator/corroboration.py
"""08 corroboration:eligible-set 重归一化透明加权求和(confidence SSOT 实现)。

契约 SSOT = 08 design §3.1/§3.2 + 决策考古 2026-06-05-08-corroboration融合方案调研与裁决.md。
W_rec/Q_bind **import 桥模块常量不复制**(SSOT 红线 G4)。
"""
from __future__ import annotations

from dataclasses import dataclass

# G4(引用不复制 SSOT 红线): W_rec/Q_bind 仍从桥模块**实时取值不复制**。改 import 模块对象、
# 用时再取 `._RECOVERY_WEIGHT`/`._STRAT_Q`(下 _score_db/_score_config),而非 import 期取名字 ——
# 五个 provider 都 `from contextos.orchestrator.provider_io import ...`,该 import 会先跑
# orchestrator/__init__(聚合 corroboration),若此处 import 期就取桥模块名字会成循环
# (桥模块尚在 line "import provider_io" 处半初始化,常量未定义)。延后取值不违 G4(常量仍读自
# 桥模块命名空间,绝不在本模块重新声明),只是把读取时机挪到函数体(init 早已完成)。
from contextos.config_dim import provider as _config_provider      # G4: 引用不复制(用时取 ._STRAT_Q)
from contextos.impact_map.enums import KIND_CONFIG_DIMENSION, KIND_SQL_DIMENSION
from contextos.lineage import provider as _lineage_provider        # G4: 引用不复制(用时取 ._RECOVERY_WEIGHT)
from contextos.orchestrator.provider_io import ProviderResult, _safe_float, _safe_int
from contextos.orchestrator.rag_projection import RagProjection
from contextos.profile.schema import CorroborationConfig

# 方法/代码维 kind(04 facade + 02 + 07)。SQL/config 维走 enums frozenset。
_METHOD_KINDS = frozenset({
    "METHOD", "CLASS", "INTERFACE", "FIELD",
    "API_ENTRY", "JOB", "BATCH", "MSG",
})


@dataclass
class CorroboratedCandidate:
    target: str
    kind: str
    score_overall: float
    confidence_tier: str
    bridge_scores: dict[str, float]
    consensus_count: int
    hit_workers: list[str]
    signals_by_worker: dict[str, dict]
    rag_score: float
    folded: bool = False


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def base_weights(cfg: CorroborationConfig) -> dict[str, float]:
    """worker_name -> 基权(design §3.1 v1 初值)。"""
    return {
        "code_search": cfg.w_code_search,
        "db_lineage_bridge": cfg.w_db_lineage,
        "config_dimension_bridge": cfg.w_config_dimension,
        "rag": cfg.w_rag,
        "dict": cfg.w_dict,
        "llm_rerank": cfg.w_llm_rerank,
    }


def eligible_bridges(kind: str) -> frozenset[str]:
    """按 candidate.kind 的枚举集合定 eligible 桥子集(design §3.1, R4 全 16 kind 覆盖)。

    method+入口 -> {code_search, llm_rerank};SQL -> {db_lineage_bridge, rag, llm_rerank};
    config -> {config_dimension_bridge, rag, llm_rerank};任何未匹配(OTHER/v2 占位/未知)
    -> 兜底 {llm_rerank}(绝不空分母 / fallthrough)。
    """
    if kind in KIND_SQL_DIMENSION:
        return frozenset({"db_lineage_bridge", "rag", "llm_rerank"})
    if kind in KIND_CONFIG_DIMENSION:
        return frozenset({"config_dimension_bridge", "rag", "llm_rerank"})
    if kind in _METHOD_KINDS:
        return frozenset({"code_search", "llm_rerank"})
    return frozenset({"llm_rerank"})


def renormalize(eligible: frozenset[str], weights: dict[str, float]) -> dict[str, float]:
    """只在 eligible 子集内重归一化,使和 = 1(design §3.1 w'_b = w_b / Σ_eligible w_b)。"""
    total = sum(weights[b] for b in eligible)
    if total <= 0:
        # 理论不可达(eligible 至少含 llm_rerank 且权重 >0);兜底均分防除零。
        return {b: 1.0 / len(eligible) for b in eligible}
    return {b: weights[b] / total for b in eligible}


def _score_db(signals: dict) -> float:
    """05 逐候选 score_bridge(design §3.1):clamp[0,1](W_rec[mode] - 0.2*branch + 0.1*[ev>=2])。"""
    mode = signals.get("recovery_mode")
    # 用时取桥模块常量(G4 引用不复制;延后取值破循环, 见顶部 import 注释)
    base = _lineage_provider._RECOVERY_WEIGHT.get(mode, 0.4) if isinstance(mode, str) else 0.4   # 未知/缺 mode -> 0.4
    if signals.get("branch_detected"):
        base -= 0.2
    if _safe_int(signals.get("evidence_count", 0)) >= 2:   # 坏类型 -> 0(fail-safe §5.1)
        base += 0.1
    return round(_clamp01(base), 4)


def _score_config(signals: dict) -> float:
    """06 逐候选 score_bridge(design §3.1):CONFIG_TABLE 0.6/0.4 owner;key 用 Q_bind[strategy]。"""
    if "table" in signals or "resolved_owner" in signals:        # CONFIG_TABLE
        return 0.6 if (signals.get("resolved_owner") or "") else 0.4
    strat = signals.get("bind_strategy")
    if not isinstance(strat, str):           # 缺 binding / llm 兜底 / 坏类型(unhashable)-> 0.3
        return 0.3
    # 用时取桥模块常量(G4 引用不复制;延后取值破循环, 见顶部 import 注释)
    return round(_config_provider._STRAT_Q.get(strat, 0.3), 4)


def score_bridge(worker_name: str, signals: dict) -> float:
    """逐候选 score_bridge 分发(design §3.1 契约表)。RAG 不走这里(投影特例 G5)。"""
    if worker_name == "code_search":
        return round(_clamp01(_safe_float(signals.get("name_match_strength", 0.0))), 4)
    if worker_name == "llm_rerank":
        return round(_clamp01(_safe_float(signals.get("vote_score", 0.0))), 4)
    if worker_name == "db_lineage_bridge":
        return _score_db(signals)
    if worker_name == "config_dimension_bridge":
        return _score_config(signals)
    return 0.0   # dict(deferred)等


def bucket(score_overall: float, consensus_count: int, cfg: CorroborationConfig) -> str:
    """confidence 分桶(design §3.2 SSOT)。

    HIGH:  score>=high AND 共识>=min(2)
    MEDIUM: 0.4<=score<0.75  OR 仅 1 桥共识  OR 高分但无共识(共识门封顶,§3.1 注)
    LOW:   其余 / 全 miss
    """
    if score_overall >= cfg.high_threshold and consensus_count >= cfg.consensus_min_bridges:
        return "HIGH"
    if consensus_count == 1 or score_overall >= cfg.medium_threshold:
        return "MEDIUM"
    return "LOW"


# RAG 投影实体名提取(design §3.1 RAG 说明:表用末段裸名 / 配置用 key 或表名)
def _rag_entity_name(target: str, kind: str, signals: dict) -> str:
    if kind in KIND_SQL_DIMENSION:
        return target.split(".")[-1]
    if kind == "CONFIG_TABLE":
        return signals.get("table") or target.split(".")[-1]
    # CONFIG_FILE/CONFIG_KEY/RULE_SET:整 key 字面匹配(不按 . 切,key 本身就是实体名)
    return target


def corroborate_one(target: str, kind: str, signals_by_worker: dict[str, dict],
                    rag_proj: RagProjection, cfg: CorroborationConfig) -> CorroboratedCandidate:
    eligible = eligible_bridges(kind)
    weights = renormalize(eligible, base_weights(cfg))

    bridge_scores: dict[str, float] = {}
    rag_score = 0.0
    for b in eligible:
        if b == "rag":
            # 投影特例(G5):不在 signals_by_worker 里找 rag,扫 snippet 字面命中实体名
            name = _rag_entity_name(target, kind, _first_signals(signals_by_worker))
            rag_score = rag_proj.score_for(name)
            bridge_scores["rag"] = rag_score
        else:
            sig = signals_by_worker.get(b)
            bridge_scores[b] = score_bridge(b, sig) if sig is not None else 0.0

    consensus_count = sum(1 for s in bridge_scores.values() if s >= cfg.consensus_score)
    bonus = cfg.alpha_consensus if consensus_count >= cfg.consensus_min_bridges else 0.0
    weighted = sum(weights[b] * bridge_scores[b] for b in eligible)
    score_overall = round(_clamp01(weighted + bonus), 4)
    tier = bucket(score_overall, consensus_count, cfg)

    hit = [w for w, sig in signals_by_worker.items() if sig is not None]
    if rag_score > 0:
        hit.append("rag")
    return CorroboratedCandidate(
        target=target, kind=kind, score_overall=score_overall, confidence_tier=tier,
        bridge_scores=bridge_scores, consensus_count=consensus_count,
        hit_workers=hit, signals_by_worker=signals_by_worker, rag_score=rag_score)


def _first_signals(signals_by_worker: dict[str, dict]) -> dict:
    for sig in signals_by_worker.values():
        if sig:
            return sig
    return {}


def corroborate(cheap_results: dict[str, ProviderResult], rerank_result: ProviderResult,
                rag_proj: RagProjection, cfg: CorroborationConfig) -> list[CorroboratedCandidate]:
    """逐 (kind, target) 跨桥对齐(RAG 候选除外,投影特例),再逐候选 corroborate_one。

    身份键 = (kind, target) 而非 target(review HIGH 1):同一物理表可同时是 05 SQL_TABLE
    与 06 CONFIG_TABLE(target 同 kind 不同),按 target 去重会吞掉一维 + 违 01 单 item 单扩展。
    两维各成独立候选(各 sql_lineage / config_binding)。
    """
    by_key: dict[tuple[str, str], dict] = {}   # (kind, target) -> {target, kind, by_worker}

    def _ingest(result: ProviderResult) -> None:
        if result is None or result.worker_name == "rag":
            return    # RAG 不进逐候选对齐(G5)
        for c in result.candidates:
            slot = by_key.setdefault((c.kind, c.target),
                                     {"target": c.target, "kind": c.kind, "by_worker": {}})
            slot["by_worker"][result.worker_name] = c.signals

    for res in cheap_results.values():
        _ingest(res)
    _ingest(rerank_result)

    return [corroborate_one(slot["target"], slot["kind"], slot["by_worker"], rag_proj, cfg)
            for slot in by_key.values()]
