"""build_impact_map_mcp_response envelope 测试。

设计思路: monkeypatch build_impact_map_impl 喂 canned impact dict, 隔离 envelope 逻辑;
另用 fake_app_ctx 跑一次真端到端验 envelope 形态(空结果也成立)。
评分标准: envelope 三键 + impact_map 可 re-parse; 默认紧凑 / full 全量; top_n clamp; 脱敏 fail-closed。
脚本逻辑: SimpleNamespace 造最小 app_ctx(只需 profile.corroboration.consensus_min_bridges)。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from contextos.impact_map.schema import ImpactMap
import contextos.mcp_server.tools.impact_map as impact_map_mod
from contextos.mcp_server.tools.impact_map import build_impact_map_mcp_response


def _canned_impact():
    # 直接构造一个合法 ImpactMap dump, 保证 impact_map 子对象能被 re-parse
    m = ImpactMap(requirement_id="r1", requirement_summary="s",
                  dimension_quality={"method": "strong"})
    dump = m.model_dump(mode="json")
    dump["evidence_items"] = [
        {"id": "a", "target": "A", "kind": "METHOD", "change_type": "modify_method",
         "confidence": 0.9, "confidence_tier": "HIGH",
         "evidence_refs": [{"source": "jdt-ls-workspaceSymbol", "rerank_score": 0.9}],
         "metadata": {"folded": False, "consensus_count": 2}},
        {"id": "b", "target": "B", "kind": "METHOD", "change_type": "modify_method",
         "confidence": 0.2, "confidence_tier": "LOW",
         "evidence_refs": [{"source": "jdt-ls-workspaceSymbol", "rerank_score": 0.2}],
         "metadata": {"folded": False, "consensus_count": 1}},
    ]
    return dump


def _ctx() -> Any:   # duck-typed AppContext 替身; -> Any 消除 stub vs AppContext 的 Pyright 噪音
    return SimpleNamespace(profile=SimpleNamespace(
        corroboration=SimpleNamespace(consensus_min_bridges=2)))


def test_envelope_shape_and_reparse(monkeypatch):
    monkeypatch.setattr(impact_map_mod, "build_impact_map_impl",
                        lambda app_ctx, **kw: _canned_impact())
    env = build_impact_map_mcp_response(_ctx(), requirement="x")
    assert set(env) == {"response_schema_version", "summary", "impact_map"}
    # impact_map 子对象仍是纯 01 schema, 可 re-parse(HIGH#3)
    ImpactMap.model_validate(env["impact_map"])


def test_default_compact_full_expands(monkeypatch):
    monkeypatch.setattr(impact_map_mod, "build_impact_map_impl",
                        lambda app_ctx, **kw: _canned_impact())
    default = build_impact_map_mcp_response(_ctx(), requirement="x")
    ids = {it["id"] for it in default["impact_map"]["evidence_items"]}
    assert ids == {"a"}                       # 默认只露强核(HIGH)
    assert default["summary"]["evidence_total"] == 2
    assert default["summary"]["truncated"] is True
    full = build_impact_map_mcp_response(_ctx(), requirement="x", full=True)
    ids_full = {it["id"] for it in full["impact_map"]["evidence_items"]}
    assert ids_full == {"a", "b"}             # full 全量
    assert full["summary"]["truncated"] is False


def test_top_n_clamped(monkeypatch):
    monkeypatch.setattr(impact_map_mod, "build_impact_map_impl",
                        lambda app_ctx, **kw: _canned_impact())
    for bad in (0, -5, 999999):
        env = build_impact_map_mcp_response(_ctx(), requirement="x", top_n=bad)
        assert "impact_map" in env


def test_sanitize_fail_closed(monkeypatch):
    leaky = _canned_impact()
    leaky["evidence_items"][0]["config_binding"] = {"value_raw": "secret123"}
    monkeypatch.setattr(impact_map_mod, "build_impact_map_impl",
                        lambda app_ctx, **kw: leaky)
    with pytest.raises(ValueError):
        build_impact_map_mcp_response(_ctx(), requirement="x")
    with pytest.raises(ValueError):
        build_impact_map_mcp_response(_ctx(), requirement="x", full=True)


def test_end_to_end_envelope_with_fake_ctx(fake_app_ctx):
    env = build_impact_map_mcp_response(fake_app_ctx, requirement="新增动态计费批量操作")
    assert set(env) == {"response_schema_version", "summary", "impact_map"}
    ImpactMap.model_validate(env["impact_map"])


def test_impl_threads_profile_corroboration_to_analyze(fake_app_ctx, monkeypatch):
    # consensus N 一致性(外部 review HIGH): build_impact_map_impl 必须把 profile.corroboration
    # 传给 analyze, 否则 pipeline 用默认 N=2 算 dimension_quality, 而 wrapper compact 用 profile N -> 自相矛盾
    fake_app_ctx.profile.corroboration.consensus_min_bridges = 3
    captured = {}

    def fake_analyze(*a, **kw):
        captured.update(kw)
        return ImpactMap(requirement_id="r", requirement_summary="s")

    monkeypatch.setattr(impact_map_mod, "analyze", fake_analyze)
    impact_map_mod.build_impact_map_impl(fake_app_ctx, requirement="x")
    assert captured["corroboration_config"] is fake_app_ctx.profile.corroboration
