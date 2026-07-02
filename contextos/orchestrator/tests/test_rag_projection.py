# contextos/orchestrator/tests/test_rag_projection.py
from contextos.orchestrator.provider_io import ProviderCandidate
from contextos.orchestrator.rag_projection import RagProjection, build_rag_projection


def test_literal_hit_word_boundary():
    p = RagProjection([("see PM_OFFER in the spec sheet", 0.9)])
    assert p.score_for("PM_OFFER") == 0.9
    assert p.score_for("pm_offer") == 0.9          # 大小写不敏感


def test_substring_not_matched():
    # 词边界防子串:PM / OFFER 都不应命中 PM_OFFER(前后是 _ 即 word char)
    p = RagProjection([("see PM_OFFER in spec", 0.9)])
    assert p.score_for("PM") == 0.0
    assert p.score_for("OFFER") == 0.0


def test_max_over_multiple_docs():
    p = RagProjection([("table foo here", 0.5), ("foo again", 0.8), ("nothing", 0.2)])
    assert p.score_for("foo") == 0.8


def test_no_hit_returns_zero():
    p = RagProjection([("unrelated text", 0.9)])
    assert p.score_for("PM_OFFER") == 0.0
    assert p.score_for("") == 0.0


def test_dotted_config_key_match():
    p = RagProjection([("set offer.switch.enable to true", 0.7)])
    assert p.score_for("offer.switch.enable") == 0.7


def test_from_candidates_builds_from_signals():
    cands = [
        ProviderCandidate(target="docs/a.md", kind="BUSINESS_DOC",
                          signals={"snippet": "CONF_PROVINCE_TAX config", "rerank_score": 0.85}),
        ProviderCandidate(target="docs/b.md", kind="BUSINESS_DOC",
                          signals={"snippet": "no entity", "rerank_score": 0.3}),
    ]
    p = build_rag_projection(cands)
    assert p.score_for("CONF_PROVINCE_TAX") == 0.85
    assert p.score_for("MISSING") == 0.0
