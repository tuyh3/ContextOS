# contextos/orchestrator/folding.py
"""折叠门控(07 §3 + 08 §3.2 LOW 默认折叠)。折叠裁决在 08 做(07 看不到全桥全貌)。

fold iff:LLM 明确反对(status=ok AND vote=oppose) AND 候选最低档(confidence_tier=LOW,
即弱线索 / 无直接强命中)AND 无多桥共识(consensus<min, LOW 已蕴含, 显式保留当 门控 a)。
门控 a:>=2 桥共识 -> 永不折叠(多桥共识压过 LLM 一票)。
门控 b:只消费 vote=oppose,不消费 status=failed/skipped(缺证据 != 反对证据)。
不永久删除:folded 只是默认不展示,数据 + 理由保留;assemble 仍把 folded 候选放进 evidence_items
(metadata.folded=True),消费方按需过滤(recall 一条不丢,review HIGH 2)。
"""
from __future__ import annotations

from contextos.orchestrator.corroboration import CorroboratedCandidate
from contextos.profile.schema import CorroborationConfig


def is_folded(cc: CorroboratedCandidate, cfg: CorroborationConfig) -> bool:
    llm_sig = cc.signals_by_worker.get("llm_rerank") or {}
    opposed = llm_sig.get("status") == "ok" and llm_sig.get("vote") == "oppose"  # 门控 b
    if not opposed:
        return False
    # 条件1(corroboration 最低/弱线索)= tier==LOW(review HIGH 2:code=1.0 是 MEDIUM 不应折);
    # 门控 a(>=2 桥共识不折)= consensus<min(LOW 已蕴含, 显式保留防回归)。
    return cc.confidence_tier == "LOW" and cc.consensus_count < cfg.consensus_min_bridges


def apply_folding(candidates: list[CorroboratedCandidate], cfg: CorroborationConfig) -> None:
    for cc in candidates:
        cc.folded = is_folded(cc, cfg)
