"""contextos init CLI 命令测试(Task 7)。

测试思路:
  CLI init 命令是薄 wrapper: 解析参数 -> load_profile -> run_init -> 打印汇总 -> exit code。
  本测试验证 CLI 接线(命令已注册 + 参数传递 + exit code 映射),不测编排逻辑(那是 Task 6)。
  run_init 全 monkeypatch 成返回预设 InitReport 的替身,不真 build 四维度。

设计:
  test_init_subcommand_registered  -- MED3: init 真注册进 app,--help 可见。
  test_init_aborts_on_missing_profile -- profile 不存在 -> load_profile 抛 -> 非 0 退出,不崩。
  test_init_exit_code_ready_zero  -- stub run_init 返 ready -> exit 0(_EXIT 映射核心契约)。
  test_init_exit_code_degraded_one -- stub run_init 返 degraded -> exit 1(非 0 分支)。
"""
import contextos.cli.init as init_cli
from contextos.init.report import InitReport
from typer.testing import CliRunner

from contextos.cli.main import app

runner = CliRunner()


def test_init_subcommand_registered():
    # MED3: init 必须真注册进 main 的 app, 否则 contextos init 不出现
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0
    assert "init" in result.output.lower()


def test_init_aborts_on_missing_profile(tmp_path):
    # MED-4: profile 缺失 -> 干净 aborted(exit 2), 不吐 traceback, 不与 degraded(1) 撞码。
    # 原实现 load_profile 在 run_init 之外裸调, FileNotFoundError 冒泡 -> exit 1 + traceback,
    # 与 degraded 同码, CI 无法区分'降级'与'profile 崩'。
    result = runner.invoke(app, ["init", "--profile", str(tmp_path / "nope.toml")])
    assert result.exit_code == 2
    assert "profile" in result.output.lower()         # 有人类可读中止原因, 非裸 traceback


def _stub_run_init(verdict):
    # load_profile + run_init 全替身: 不真 build, 只验 verdict -> exit code 这条链
    def _run(prof, *, now, only=None, skip_oracle=False):
        return InitReport(steps=[], verdict=verdict, reasons=[])
    return _run


def test_init_exit_code_ready_zero(monkeypatch):
    monkeypatch.setattr(init_cli, "load_profile", lambda _p: object())
    monkeypatch.setattr(init_cli, "run_init", _stub_run_init("ready"))
    result = runner.invoke(app, ["init", "--profile", "/whatever.toml"])
    assert result.exit_code == 0


def test_init_exit_code_degraded_one(monkeypatch):
    monkeypatch.setattr(init_cli, "load_profile", lambda _p: object())
    monkeypatch.setattr(init_cli, "run_init", _stub_run_init("degraded"))
    result = runner.invoke(app, ["init", "--profile", "/whatever.toml"])
    assert result.exit_code == 1
