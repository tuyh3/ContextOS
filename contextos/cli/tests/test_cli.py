"""contextos CLI(Typer)测试 —— Plan 10 Task 10。

测试思路:
  CLI 三命令(serve-mcp / query / run-evaluation)都是薄 wrapper,真正的重活在
  load_profile -> AppContext.from_profile -> build_server / build_impact_map_impl。
  本测试**只验证 CLI 接线**(参数解析 + 调对下游 + 输出形态),不验证 02 breakdown /
  编排准度(那是 server smoke + 人工 smoke 的事)。故所有下游(load_profile /
  AppContext / build_impact_map_impl / build_server / mcp.run)全 monkeypatch 成轻量
  替身,CLI 测试**绝不**真起 JDT / Oracle / RAG / stdio。

  典型坑:Typer 命令 import 的是 main 模块顶层符号(`from ... import build_impact_map_impl`),
  monkeypatch 必须打在 `contextos.cli.main.<symbol>` 上,不能打在原模块上(否则 main 里
  绑定的旧引用不受影响)。

serve-mcp 怎么避开 stdio 阻塞:
  真 serve-mcp 末尾 mcp.run(transport="stdio") 会阻塞进程等 stdin/stdout(MCP 协议
  长连接),单测里一旦调到就永久挂起。故 monkeypatch build_server 返回一个 _RecordingMcp
  替身:它的 run() 只记录被调用 + transport 实参,**立即返回不阻塞**。CliRunner.invoke
  正常拿到 exit_code 0,我们再断言 run 确实被调到且 transport=='stdio' —— 既证明 CLI
  能走到"起 server"那一步不崩,又不触发真 stdio 长连接。

评分标准:
  - --help:列出 serve-mcp / query / run-evaluation 三命令,exit_code 0。
  - query:stdout 是合法 JSON(json.loads 不抛)且含 evidence_items 键,exit_code 0;
    且 load_profile 被调(profile 解析接线正确)。
  - run-evaluation:stdout 含 "Plan 09"(deferred 占位指针),exit_code 0。
  - serve-mcp:mcp.run 被调到且 transport=='stdio',不实际阻塞,exit_code 0。
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


# --------------------------------------------------------------------------
# 轻量替身 + 公共 monkeypatch 接线
# --------------------------------------------------------------------------

class _FakeProfile:
    """load_profile 返回值替身;CLI 只把它透传给 AppContext.from_profile,不读字段。"""


class _FakeAppCtx:
    """AppContext 替身。from_profile 是 classmethod 入口(04b T14: JDT 预热已删,
    serve-mcp 启动只建 ctx + build_server, 替身无需任何资源属性)。"""

    @classmethod
    def from_profile(cls, profile: Any) -> "_FakeAppCtx":
        return cls()


class _RecordingMcp:
    """build_server 返回值替身。run() 记录调用 + transport,立即返回(不阻塞 stdio)。"""

    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []

    def run(self, *, transport: str = "stdio") -> None:
        self.run_calls.append({"transport": transport})
        # 关键:立即返回,不进 stdio 长连接事件循环 -> 单测不挂起。


# 真 01 ImpactMap 顶层形状(schema.py ImpactMap): CLI query 只 json.dumps 透传此 stub,
# 但用真字段名避免误导读者以为 schema 长这样(feedback_test_fixtures_match_real_contract)。
_FAKE_IMPACT_MAP: dict[str, Any] = {
    "requirement_id": "req-stub-001",
    "requirement_summary": "stub requirement",
    "version": "1.0",
    "dimension_status": {"method": "resolved", "sql_table": "resolved", "config": "resolved"},
    "known_limitations": [],
    "evidence_items": [
        {"id": "ev-001", "target": "FooSvc.bar", "kind": "METHOD", "confidence": "high"}
    ],
}


@pytest.fixture
def patched_profile_and_ctx(monkeypatch):
    """把 load_profile + AppContext 打成替身,记录 load_profile 是否被调。

    返回一个 dict,'load_calls' 累计 load_profile 调用的 profile 实参(断言接线)。
    """
    calls: dict[str, list[Any]] = {"load_calls": []}

    def _fake_load_profile(path: Any = None) -> _FakeProfile:
        calls["load_calls"].append(path)
        return _FakeProfile()

    monkeypatch.setattr(cli_main, "load_profile", _fake_load_profile)
    monkeypatch.setattr(cli_main, "AppContext", _FakeAppCtx)
    return calls


# --------------------------------------------------------------------------
# --help
# --------------------------------------------------------------------------

def test_help_lists_all_commands():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "serve-mcp" in res.stdout
    assert "query" in res.stdout
    assert "run-evaluation" in res.stdout


# --------------------------------------------------------------------------
# query
# --------------------------------------------------------------------------

def test_query_prints_valid_json_with_evidence_items(patched_profile_and_ctx, monkeypatch):
    monkeypatch.setattr(
        cli_main, "build_impact_map_impl",
        lambda app_ctx, *, requirement, **kw: dict(_FAKE_IMPACT_MAP),
    )
    res = runner.invoke(app, ["query", "新增动态计费批量操作"])
    assert res.exit_code == 0, res.output
    # stdout 必须是合法 JSON(json.loads 不抛)
    parsed = json.loads(res.stdout)
    assert "evidence_items" in parsed
    assert parsed["version"] == "1.0"
    # load_profile 被调一次(profile 解析接线正确)
    assert len(patched_profile_and_ctx["load_calls"]) == 1


def test_query_passes_requirement_to_impl(patched_profile_and_ctx, monkeypatch):
    seen: dict[str, Any] = {}

    def _capture(app_ctx, *, requirement, **kw):
        seen["requirement"] = requirement
        return dict(_FAKE_IMPACT_MAP)

    monkeypatch.setattr(cli_main, "build_impact_map_impl", _capture)
    res = runner.invoke(app, ["query", "先付费对应的方法是什么"])
    assert res.exit_code == 0, res.output
    assert seen["requirement"] == "先付费对应的方法是什么"


# --------------------------------------------------------------------------
# run-evaluation(v1.x deferred 占位)
# --------------------------------------------------------------------------

def test_run_evaluation_placeholder_points_to_plan09():
    res = runner.invoke(app, ["run-evaluation"])
    assert res.exit_code == 0
    assert "Plan 09" in res.stdout


# --------------------------------------------------------------------------
# serve-mcp(不真起 stdio:monkeypatch build_server -> run 立即返回)
# --------------------------------------------------------------------------

def _stub_watcher(monkeypatch) -> list[Any]:
    """04b T15: serve-mcp 启动会调 start_projection_watch(watcher + 补课线程)。
    CLI 测试不起真 watchdog/线程 -> 打 no-op 替身(照 T14 对 prewarm 替身的处理方式)。
    注意 main.py 是函数内 import, 必须 patch watcher 模块本体而非 cli_main 顶层符号。
    返回 calls 列表供断言接线(被调到 + 实参是 app_ctx)。"""
    calls: list[Any] = []
    monkeypatch.setattr(
        "contextos.code_intel.projection.watcher.start_projection_watch",
        lambda app_ctx: calls.append(app_ctx))
    return calls


def test_serve_mcp_runs_stdio_without_blocking(patched_profile_and_ctx, monkeypatch):
    recorder = _RecordingMcp()
    watch_calls = _stub_watcher(monkeypatch)
    monkeypatch.setattr(cli_main, "build_server", lambda app_ctx: recorder)
    res = runner.invoke(app, ["serve-mcp"])
    assert res.exit_code == 0, res.output
    # run 被调到(走到了"起 server"那一步),且用 stdio transport,但没真阻塞
    assert recorder.run_calls == [{"transport": "stdio"}]
    # watcher 接线被调到一次(T15), 实参就是 AppContext 替身
    assert len(watch_calls) == 1
    assert isinstance(watch_calls[0], _FakeAppCtx)


def test_serve_mcp_loads_profile(patched_profile_and_ctx, monkeypatch):
    recorder = _RecordingMcp()
    _stub_watcher(monkeypatch)
    monkeypatch.setattr(cli_main, "build_server", lambda app_ctx: recorder)
    res = runner.invoke(app, ["serve-mcp", "--profile", "/tmp/p.toml"])
    assert res.exit_code == 0, res.output
    # --profile 透传给 load_profile
    assert len(patched_profile_and_ctx["load_calls"]) == 1
    assert Path(patched_profile_and_ctx["load_calls"][0]) == Path("/tmp/p.toml")


# --------------------------------------------------------------------------
# rebuild(LOW-1 最终 review: spec §5.3 承诺 MCP tool + CLI 双入口)
# --------------------------------------------------------------------------
# 设计思路: rebuild 是薄命令 —— load_profile -> engine_from_profile ->
# rebuild_entry.incremental_rebuild_code(lockfile=data_dir/projection.lock, 与
# app_context.projection_lockfile 同口径)-> 打印结果 JSON + 按 status 退出码。
# 全 monkeypatch 下游(不真起 git/jar/engine), 只验接线 + 退出码契约:
# ok/noop=0, already_running/degraded=1, 非 code scope=not_implemented+1。


class _FakeStorageNS:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir


class _FakeRebuildProfile:
    def __init__(self, data_dir: str) -> None:
        self.storage = _FakeStorageNS(data_dir)


def _patch_rebuild(monkeypatch, tmp_path, result: dict) -> dict:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli_main, "load_profile",
                        lambda path=None: _FakeRebuildProfile(str(tmp_path / "dd")))
    monkeypatch.setattr(cli_main, "engine_from_profile", lambda p: "ENGINE")

    def _fake_entry(profile, engine, *, lockfile):
        seen["engine"] = engine
        seen["lockfile"] = lockfile
        return dict(result)

    monkeypatch.setattr(cli_main, "incremental_rebuild_code", _fake_entry)
    return seen


def test_help_lists_rebuild():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "rebuild" in res.stdout


def test_rebuild_ok_exit_zero_and_lockfile_path(monkeypatch, tmp_path):
    from pathlib import Path as _P
    seen = _patch_rebuild(monkeypatch, tmp_path, {"status": "ok", "reparsed": 2})
    res = runner.invoke(app, ["rebuild"])
    assert res.exit_code == 0, res.output
    assert seen["engine"] == "ENGINE"                      # engine_from_profile 接线
    assert seen["lockfile"] == _P(str(tmp_path / "dd")) / "projection.lock"
    assert json.loads(res.stdout)["status"] == "ok"        # 结果 dict 打印


def test_rebuild_noop_exit_zero(monkeypatch, tmp_path):
    _patch_rebuild(monkeypatch, tmp_path, {"status": "noop", "detail": "no changes"})
    res = runner.invoke(app, ["rebuild"])
    assert res.exit_code == 0, res.output


def test_rebuild_already_running_exit_one(monkeypatch, tmp_path):
    _patch_rebuild(monkeypatch, tmp_path, {"status": "already_running"})
    res = runner.invoke(app, ["rebuild"])
    assert res.exit_code == 1
    assert "already_running" in res.stdout


def test_rebuild_degraded_exit_one(monkeypatch, tmp_path):
    _patch_rebuild(monkeypatch, tmp_path, {"status": "degraded", "detail": "x"})
    res = runner.invoke(app, ["rebuild"])
    assert res.exit_code == 1


def test_rebuild_non_code_scope_not_implemented(monkeypatch, tmp_path):
    seen = _patch_rebuild(monkeypatch, tmp_path, {"status": "ok"})
    res = runner.invoke(app, ["rebuild", "--scope", "rag"])
    assert res.exit_code == 1
    assert "not_implemented" in res.stdout
    assert "lockfile" not in seen                          # 下游未被调


# --------------------------------------------------------------------------
# query --adapter-kind 透传(Task 9)
# --------------------------------------------------------------------------
# 设计思路: query 命令新增 --adapter-kind 选项(默认 text), 透传给 build_impact_map_impl
# 的 adapter_kind 参数; .eml 文件走 "email", .docx 走 "docx"(用户显式声明, 非自动探测)。
# 评分标准: fake_build 捕获 adapter_kind 实参 == "email"; exit_code 0。
# 脚本逻辑: monkeypatch build_impact_map_impl / load_profile / AppContext.from_profile,
# 用 CliRunner 调 ["query", "x.eml", "--adapter-kind", "email"], 断言捕获值。
# 注: patch 目标是 contextos.cli.main 顶层符号(main.py 顶部 from ... import 方式绑定);
# AppContext 作为类打在 cli_main.AppContext 上, from_profile 是 staticmethod 替换。

def test_cli_query_passes_adapter_kind(monkeypatch):
    """CLI query 的 --adapter-kind 透传到 build_impact_map_impl。"""
    captured: dict = {}

    def fake_build(app_ctx, *, requirement, adapter_kind="text", top_n=50, corpora=None):
        captured["adapter_kind"] = adapter_kind
        return {"ok": True, "evidence_items": []}

    monkeypatch.setattr(cli_main, "build_impact_map_impl", fake_build)
    monkeypatch.setattr(cli_main, "load_profile", lambda *a, **k: object())
    monkeypatch.setattr(cli_main.AppContext, "from_profile", staticmethod(lambda p: object()))
    res = CliRunner().invoke(cli_main.app, ["query", "x.eml", "--adapter-kind", "email"])
    assert res.exit_code == 0, res.output
    assert captured.get("adapter_kind") == "email"


def test_cli_query_unknown_adapter_kind_errors_cleanly(monkeypatch):
    """未知 --adapter-kind 时 query 应输出可读错误并非零退出, 不是空白 exit-1。
    设计思路: build_impact_map_impl 抛 ValueError(get_adapter 对未注册 kind 的真实行为),
    query 须捕获并 echo 到 stderr + Exit(1)。
    评分标准: exit_code != 0 且输出含错误文案(非空白)。
    脚本逻辑: monkeypatch build_impact_map_impl 抛 ValueError, CliRunner invoke, 断言退出码+输出。
    """
    from typer.testing import CliRunner
    from contextos.cli import main as cli_main

    def boom(app_ctx, *, requirement, adapter_kind="text", top_n=50, corpora=None):
        raise ValueError(f"unsupported source_kind: '{adapter_kind}'")

    monkeypatch.setattr(cli_main, "build_impact_map_impl", boom)
    monkeypatch.setattr(cli_main, "load_profile", lambda *a, **k: object())
    monkeypatch.setattr(cli_main.AppContext, "from_profile", staticmethod(lambda p: object()))
    res = CliRunner().invoke(cli_main.app, ["query", "x.txt", "--adapter-kind", "bogus"])
    assert res.exit_code != 0
    assert "bogus" in res.output or "unsupported" in res.output or "Error" in res.output
