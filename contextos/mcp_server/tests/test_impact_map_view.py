"""impact_map_view 纯函数测试(紧凑视图 / 摘要 / 脱敏)。

设计思路: 这些函数对 model_dump 出的 dict 操作, 不依赖 app_ctx, 可纯单测。
评分标准: 强核线只留 HIGH/consensus>=N; folded 排除; 每维度 top_n; 空核兜底放开 folded。
脚本逻辑: 手搓 evidence dict(中性合成), 断言筛选结果。
"""
from __future__ import annotations

import pytest

from contextos.mcp_server.tools.impact_map_view import compact_evidence, summarize, verify_no_sensitive


def _ev(eid, kind, tier, consensus, *, folded=False, conf=0.5):
    return {"id": eid, "kind": kind, "confidence_tier": tier, "confidence": conf,
            "metadata": {"folded": folded, "consensus_count": consensus}}


def test_compact_keeps_strong_excludes_folded():
    items = [
        _ev("a", "METHOD", "HIGH", 2),                 # 强核 keep
        _ev("b", "METHOD", "LOW", 1),                  # 弱 -> 丢
        _ev("c", "CONFIG_KEY", "LOW", 1, folded=True), # folded -> 丢
        _ev("d", "SQL_TABLE", "MEDIUM", 2),            # consensus>=2 -> keep
    ]
    out, empty_core = compact_evidence(items, consensus_min_bridges=2, top_n=50)
    assert empty_core is False
    assert {it["id"] for it in out} == {"a", "d"}


def test_compact_per_dimension_top_n_high_not_cut():
    items = [_ev(f"m{i}", "METHOD", "HIGH", 2, conf=0.9 - i * 0.01) for i in range(5)]
    out, _ = compact_evidence(items, consensus_min_bridges=2, top_n=3)
    assert len(out) == 3   # 每维度 cap 3, 都是 HIGH 不被截光


def test_compact_empty_core_falls_back_to_full_incl_folded():
    items = [
        _ev("x", "CONFIG_KEY", "LOW", 1, folded=True),
        _ev("y", "CONFIG_KEY", "LOW", 1),
    ]
    out, empty_core = compact_evidence(items, consensus_min_bridges=2, top_n=50)
    assert empty_core is True
    assert {it["id"] for it in out} == {"x", "y"}   # 空核分支放开 folded


def test_compact_empty_input_not_flagged_as_empty_core():
    # 真无 evidence != 弱线索被折叠: empty_core 必须 False(不误导 full=true)
    out, empty_core = compact_evidence([], consensus_min_bridges=2, top_n=50)
    assert out == []
    assert empty_core is False


# ---------------------------------------------------------------------------
# Task 6: summarize + verify_no_sensitive 测试
# ---------------------------------------------------------------------------

def _ev_full(eid, kind, tier, sources):
    return {"id": eid, "kind": kind, "confidence_tier": tier, "confidence": 0.5,
            "metadata": {"folded": False, "consensus_count": 1},
            "evidence_refs": [{"source": s, "rerank_score": 0.0} for s in sources]}


def _impact(evidence, **kw):
    base = {"evidence_items": evidence, "dimension_quality": {}, "dimension_status": {},
            "candidate_entrypoints": [], "modules_touched": [], "relations": []}
    base.update(kw)
    return base


def test_summarize_two_bridge_counts_honesty():
    # 一条 config 候选: domain=ripgrep 定位 + 带 llm-rerank ref
    ev = [_ev_full("a", "CONFIG_KEY", "LOW", ["ripgrep-config-fallback", "llm-rerank"])]
    s = summarize(_impact(ev, dimension_quality={"config": "fallback_only"}),
                  returned=1, full=False, empty_core_fallback=False)
    assert s["by_source_ref"]["llm-rerank"] == 1          # 谁跑过(含 llm)
    assert "llm-rerank" not in s["by_domain_source"]      # 谁真定位(排 llm)
    assert s["by_domain_source"]["ripgrep-config-fallback"] == 1
    assert s["dimension_quality"] == {"config": "fallback_only"}


def test_summarize_field_coverage_registry_driven():
    s = summarize(_impact([], candidate_entrypoints=[{"kind": "API", "target": "x"}]),
                  returned=0, full=False, empty_core_fallback=False)
    assert s["field_coverage"]["candidate_entrypoints"] == "populated"
    assert s["field_coverage"]["modules_touched"] == "not_populated_in_v1"
    s2 = summarize(_impact([]), returned=0, full=False, empty_core_fallback=False)
    assert s2["field_coverage"]["candidate_entrypoints"] == "none_found"


def test_summarize_truncated_and_recommended_use_redline():
    ev = [_ev_full(f"e{i}", "METHOD", "LOW", ["jdt-ls-workspaceSymbol"]) for i in range(5)]
    s = summarize(_impact(ev), returned=2, full=False, empty_core_fallback=False)
    assert s["evidence_total"] == 5
    assert s["truncated"] is True
    assert "how_to_get_full" in s
    ru = s["recommended_use"]
    assert "full=true" in ru
    for bad in ("只信", "仅信", "丢弃", "扔掉", "忽略其余"):
        assert bad not in ru


def test_summarize_empty_core_note_appended():
    # 空核兜底(有 evidence 但无强核): 提示含被折叠弱线索 + 引导 full=true
    ev = [_ev_full("a", "CONFIG_KEY", "LOW", ["ripgrep-config-fallback"])]
    s = summarize(_impact(ev), returned=1, full=False, empty_core_fallback=True)
    assert "full=true" in s["recommended_use"]
    assert "折叠" in s["recommended_use"]


def test_summarize_no_evidence_not_misleading():
    # 真无 evidence(total==0): 不引导 full=true, 不说"被折叠"(MEDIUM#4)
    s = summarize(_impact([]), returned=0, full=False, empty_core_fallback=False)
    assert s["evidence_total"] == 0
    assert s["truncated"] is False
    assert "full=true" not in s["recommended_use"]
    assert "未产出" in s["recommended_use"]


def test_verify_no_sensitive_catches_value_bearing_fields():
    for leak in (
        {"x": [{"config_binding": {"value_raw": "secret123"}}]},
        {"x": [{"evidence_refs": [{"source": "s", "content_raw": "password=hunter2"}]}]},
        {"x": [{"evidence_refs": [{"source": "s", "content_summary": "token=abc"}]}]},
    ):
        with pytest.raises(ValueError):
            verify_no_sensitive(leak)


def test_verify_no_sensitive_allows_key_name_in_target():
    # 配置 key 名字面含 'password' 是合法标识符(非值泄漏), 不应误报(HIGH#2 边界)
    verify_no_sensitive({"impact_map": {"evidence_items": [
        {"target": "spring.datasource.password", "config_binding": {"value_raw": None}}]}})
