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

L1c(spec 附录 A.4, 旗标中性化 --skip-oracle -> --skip-db):
  test_init_skip_db_flag_passes_through   -- 新旗标 --skip-db 传入 -> run_init 收到 skip_db=True。
  test_init_skip_oracle_alias_still_works -- 旧旗标 --skip-oracle 是兼容别名, 效果同 --skip-db;
                                             同传两旗标也不冲突(bool 开关幂等)。别名下线时删本测试。
  评分标准: run_init 的 kwargs 里 skip_db 为 True 且 exit code 不受旗标影响;
  自动逻辑: stub run_init 捕获 kwargs(不真 build), 中性 profile 路径。
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
    def _run(prof, *, now, only=None, skip_db=False):
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


def _capture_run_init(seen):
    # 捕获 run_init 收到的 skip_db(旗标 -> 形参接线验证), 不真 build
    def _run(prof, *, now, only=None, skip_db=False):
        seen["skip_db"] = skip_db
        return InitReport(steps=[], verdict="ready", reasons=[])
    return _run


def test_init_skip_db_flag_passes_through(monkeypatch):
    # L1c: 新旗标 --skip-db -> run_init(skip_db=True)
    seen: dict = {}
    monkeypatch.setattr(init_cli, "load_profile", lambda _p: object())
    monkeypatch.setattr(init_cli, "run_init", _capture_run_init(seen))
    result = runner.invoke(app, ["init", "--profile", "/whatever.toml", "--skip-db"])
    assert result.exit_code == 0
    assert seen["skip_db"] is True


def test_init_skip_oracle_alias_still_works(monkeypatch):
    # L1c 兼容别名: --skip-oracle(已废弃)效果同 --skip-db; 同传两旗标不冲突(bool 幂等)。
    # 别名一个过渡期后下线, 届时删本测试。
    seen: dict = {}
    monkeypatch.setattr(init_cli, "load_profile", lambda _p: object())
    monkeypatch.setattr(init_cli, "run_init", _capture_run_init(seen))
    result = runner.invoke(app, ["init", "--profile", "/whatever.toml", "--skip-oracle"])
    assert result.exit_code == 0
    assert seen["skip_db"] is True
    seen.clear()
    result = runner.invoke(app, ["init", "--profile", "/whatever.toml",
                                 "--skip-db", "--skip-oracle"])
    assert result.exit_code == 0
    assert seen["skip_db"] is True
