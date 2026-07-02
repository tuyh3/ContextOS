# contextos/orchestrator/tests/test_assemble_code_projection.py
"""04b freshness 三键 -> EvidenceItem.metadata.code_projection(spec §9)。

live-JDT 兼容: code_sig 无 freshness 键(或全空串)时 metadata 不出现 code_projection。
"""
from contextos.orchestrator.assemble import to_evidence_item
from contextos.orchestrator.corroboration import CorroboratedCandidate


def _cc(code_sig: dict) -> CorroboratedCandidate:
    return CorroboratedCandidate(
        target="com.x.Foo#bar", kind="METHOD", score_overall=0.9,
        confidence_tier="HIGH", bridge_scores={"code_search": 1.0},
        consensus_count=1, hit_workers=["code_search"],
        signals_by_worker={"code_search": code_sig}, rag_score=0.0)


def test_freshness_keys_become_code_projection_metadata():
    cc = _cc({"name_match_strength": 1.0, "file": "a.java",
              "line_start": 10, "line_end": 20,
              "projection_build_id": "b1", "indexed_commit": "c0ffee",
              "projection_status": "ok"})
    ev = to_evidence_item(cc, "ev0000", ["add"])
    assert ev.metadata["code_projection"] == {
        "projection_build_id": "b1", "indexed_commit": "c0ffee",
        "projection_status": "ok"}


def test_no_freshness_keys_no_code_projection_metadata():
    # live JDT 路径: 信号无 freshness 键 / 或 schema 默认全空串
    ev = to_evidence_item(
        _cc({"name_match_strength": 1.0, "file": "a.java"}), "ev0001", ["add"])
    assert "code_projection" not in ev.metadata

    ev2 = to_evidence_item(
        _cc({"name_match_strength": 1.0, "file": "a.java",
             "projection_build_id": "", "indexed_commit": "",
             "projection_status": ""}), "ev0002", ["add"])
    assert "code_projection" not in ev2.metadata
