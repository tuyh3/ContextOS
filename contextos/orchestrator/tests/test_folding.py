# contextos/orchestrator/tests/test_folding.py
from contextos.orchestrator.corroboration import CorroboratedCandidate
from contextos.orchestrator.folding import apply_folding, is_folded
from contextos.profile.schema import CorroborationConfig

CFG = CorroborationConfig()


def _cc(consensus, llm_sig, tier="LOW"):
    return CorroboratedCandidate(
        target="t", kind="METHOD", score_overall=0.3, confidence_tier=tier,
        bridge_scores={}, consensus_count=consensus, hit_workers=[],
        signals_by_worker={"llm_rerank": llm_sig}, rag_score=0.0)


def test_fold_when_oppose_low_and_no_consensus():
    assert is_folded(_cc(1, {"vote": "oppose", "status": "ok"}, tier="LOW"), CFG) is True


def test_no_fold_when_medium_strong_signal():
    # review HIGH 2:code=1.0 + llm oppose -> MEDIUM,不折(强名字命中非弱线索)
    assert is_folded(_cc(1, {"vote": "oppose", "status": "ok"}, tier="MEDIUM"), CFG) is False


def test_no_fold_when_two_bridge_consensus():
    # 门控 a:>=2 桥共识即便 oppose 也不折叠
    assert is_folded(_cc(2, {"vote": "oppose", "status": "ok"}), CFG) is False


def test_no_fold_on_failed_or_skipped():
    # 门控 b:status=failed/skipped 不算反对(缺证据 != 反对证据)
    assert is_folded(_cc(1, {"vote": "abstain", "status": "failed"}), CFG) is False
    assert is_folded(_cc(1, {"vote": "abstain", "status": "skipped"}), CFG) is False


def test_no_fold_on_support_or_abstain():
    assert is_folded(_cc(1, {"vote": "support", "status": "ok"}), CFG) is False
    assert is_folded(_cc(1, {"vote": "abstain", "status": "ok"}), CFG) is False


def test_no_fold_when_no_llm_signal():
    cc = CorroboratedCandidate(target="t", kind="METHOD", score_overall=0.3,
        confidence_tier="LOW", bridge_scores={}, consensus_count=1, hit_workers=[],
        signals_by_worker={}, rag_score=0.0)
    assert is_folded(cc, CFG) is False


def test_apply_folding_sets_flag():
    cc = _cc(1, {"vote": "oppose", "status": "ok"})
    apply_folding([cc], CFG)
    assert cc.folded is True
