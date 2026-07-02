# contextos/orchestrator/tests/test_artifact.py
import json
from datetime import datetime

from contextos.impact_map.schema import ImpactMap
from contextos.orchestrator.artifact import make_run_id, write_run_artifact
from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.requirement.schema import RequirementBreakdown


def test_make_run_id_format():
    rid = make_run_id("Dynamic Charging Enhancements",
                      now=datetime(2026, 5, 29, 14, 30, 52), short_hash="a3f9c1")
    assert rid == "20260529-143052-dynamic-charging-enhancements-a3f9c1"


def test_write_run_artifact_full_structure(tmp_path):
    bd = RequirementBreakdown(requirement_id="r1", raw_text="add charging", source_kind="text")
    im = ImpactMap(requirement_id="r1", requirement_summary="add charging")
    cheap = {"code_search": ProviderResult(worker_name="code_search", score=1.0)}
    run_dir = write_run_artifact(
        tmp_path, "20260529-143052-x-abc",
        raw_input="add charging", breakdown=bd, impact_map=im, cheap_results=cheap,
        rerank_result=ProviderResult(worker_name="llm_rerank", score=0.0, miss_reason="no_candidates"),
        corrobs=[], trace=["t1"], errors=[], summary_meta={"status": "ok"})
    for rel in ("summary.json", "impact_map.json", "input/source.txt", "input/02_parsed.json",
                "providers/code_search.json", "providers/llm_rerank.json",
                "corroboration.json", "change_type.json", "trace.log", "errors.log"):
        assert (run_dir / rel).exists(), rel
    assert (run_dir / "input" / "source.txt").read_text(encoding="utf-8") == "add charging"
    assert (run_dir / "trace.log").read_text(encoding="utf-8") == "t1"
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["run_id"] == "20260529-143052-x-abc"
    assert summary["status"] == "ok"


def test_providers_dump_redacts_sensitive_signal_values(tmp_path):
    # 敏感值脱敏防御纵深(WF3 pipeline-probe driven):provider signals 若混入 value-bearing 字段,
    # 落盘 providers/*.json 必须掩掉原值(真路径 06 不吐这些, 此为持久化边界兜底 + 护 Plan 10 MCP)。
    leaky = ProviderResult(worker_name="config_dimension_bridge", score=0.6, candidates=[
        ProviderCandidate(target="db.password", kind="CONFIG_KEY", signals={
            "entity_key": "db.password", "bind_strategy": "exact_match",
            "value_raw": "S3CR3T-PASSWORD-LEAK-9f3a1", "db_snapshot": "ROW_DATA_LEAK_xyz789",
            "snapshot_value": "ROW_DATA_LEAK_xyz789"})])
    bd = RequirementBreakdown(requirement_id="r1", raw_text="x", source_kind="text")
    im = ImpactMap(requirement_id="r1", requirement_summary="x")
    run_dir = write_run_artifact(
        tmp_path, "20260529-143052-x-abc", raw_input="x", breakdown=bd, impact_map=im,
        cheap_results={"config_dimension_bridge": leaky}, rerank_result=None,
        corrobs=[], trace=[], errors=[], summary_meta={"status": "ok"})
    dumped = (run_dir / "providers" / "config_dimension_bridge.json").read_text(encoding="utf-8")
    assert "S3CR3T-PASSWORD-LEAK-9f3a1" not in dumped       # 原值绝不落盘
    assert "ROW_DATA_LEAK_xyz789" not in dumped
    assert "[redacted:red-line-2]" in dumped                # 掩码标记可见
    assert "db.password" in dumped                          # 键名(非值)+ 结构信号保留供 audit
    assert "exact_match" in dumped
