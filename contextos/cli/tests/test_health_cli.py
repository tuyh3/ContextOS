"""contextos CLI `health` 子命令测试 —— ops-localization 组件 C Task 1。

测试思路:
  health 是薄 wrapper —— load_profile -> AppContext.from_profile -> 组合
  health_check_impl(app_ctx) + profile_info_impl(app_ctx) -> json.dumps 打 stdout。
  本测试只验 CLI 接线(参数解析 + 调对下游 + 输出形态), 不验探活准度
  (那是 meta.py 单测 + 人工 smoke 的事)。故下游 impl 全 monkeypatch 成轻量替身,
  CLI 测试绝不真起 JDT / Oracle / RAG。

  典型坑: health 命令 import 的是 main 模块顶层符号
  (`from ...meta import health_check_impl`), monkeypatch 必须打在
  `contextos.cli.main.<symbol>` 上, 不能打原模块。

评分标准:
  - --help: 列出 health 命令, exit_code 0。
  - health: stdout 合法 JSON(json.loads 不抛), 顶层有 "health" 和 "profile_info"
    两键, 值分别是两个 impl 的返回, exit_code 0; 且 load_profile 被调一次。
  - health 的 --profile 透传给 load_profile。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import contextos.cli.main as cli_main
from contextos.cli.main import app

runner = CliRunner()


class _FakeProfile:
    pass


class _FakeAppCtx:
    @classmethod
    def from_profile(cls, profile: Any) -> "_FakeAppCtx":
        return cls()


_FAKE_HEALTH: dict[str, Any] = {
    "jdt_ls": "cold", "oracle": "offline", "models": "lazy",
    "engine": "ok", "code_projection": {"status": "not_built"}, "ripgrep": "ok",
}
_FAKE_PROFILE_INFO: dict[str, Any] = {
    "profile_path": "<not set>", "data_dir": "/x/database",
    "repo_root": "/x/repo", "source_roots": ["/x/repo/src"],
    "oracle_instances": [], "rag_corpora": ["confirmed-cases"],
    "missing_required": [], "dispatch_patterns": [], "carrier_read_patterns": [],
}


@pytest.fixture
def patched(monkeypatch):
    calls: dict[str, list[Any]] = {"load_calls": []}

    def _fake_load_profile(path: Any = None) -> _FakeProfile:
        calls["load_calls"].append(path)
        return _FakeProfile()

    monkeypatch.setattr(cli_main, "load_profile", _fake_load_profile)
    monkeypatch.setattr(cli_main, "AppContext", _FakeAppCtx)
    monkeypatch.setattr(cli_main, "health_check_impl", lambda app_ctx: dict(_FAKE_HEALTH))
    monkeypatch.setattr(cli_main, "profile_info_impl", lambda app_ctx: dict(_FAKE_PROFILE_INFO))
    return calls


def test_help_lists_health():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "health" in res.stdout


def test_health_prints_combined_json(patched):
    res = runner.invoke(app, ["health"])
    assert res.exit_code == 0, res.output
    parsed = json.loads(res.stdout)
    assert set(parsed.keys()) == {"health", "profile_info"}
    assert parsed["health"]["engine"] == "ok"
    assert parsed["profile_info"]["rag_corpora"] == ["confirmed-cases"]
    assert len(patched["load_calls"]) == 1


def test_health_passes_profile(patched):
    res = runner.invoke(app, ["health", "--profile", "/tmp/p.toml"])
    assert res.exit_code == 0, res.output
    assert Path(patched["load_calls"][0]) == Path("/tmp/p.toml")
