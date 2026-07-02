"""SessionStart hook 脚本(scripts/contextos-health-sessionstart.sh)行为测试 ——
ops-localization 组件 C Task 4。

测试思路:
  hook 脚本是纯 bash。"Claude Code 真触发 SessionStart" 那一环无法单测(依赖运行时,
  见 examples/hooks/README.md §6 可手动验证步骤)。但脚本的可单测部分 —— 成功注入格式 /
  各 fail-open 降级分支 / timeout / JSON 转义 —— 用 subprocess 调脚本验证。

  关键技巧: 用一个临时 PATH 注入假 `contextos` 可执行(bash stub), 控制它的 stdout /
  退出码 / 是否慢, 来驱动脚本走不同分支。脚本读 CONTEXTOS_BIN / CONTEXTOS_HEALTH_TIMEOUT
  环境变量, 测试用它们控制。

评分标准:
  - 成功路径: 假 contextos 吐 JSON -> 脚本 stdout 是合法 SessionStart hook JSON,
    hookEventName=="SessionStart", additionalContext 解析回来 == 假 contextos 的 JSON,
    退出码 0。
  - 命令缺失: PATH 里没 contextos -> 退出码 0(fail-open), additionalContext 含 "探活不可用"。
  - 非零退出: 假 contextos exit 1 -> 退出码 0, additionalContext 含 "非零退出"。
  - 空输出: 假 contextos 啥都不吐 -> 退出码 0, additionalContext 含 "为空"。
  - 超时: 假 contextos sleep 长于 CONTEXTOS_HEALTH_TIMEOUT -> 退出码 0,
    additionalContext 含 "超时"。
  - 引号转义: 假 contextos 吐含双引号的 JSON -> 脚本 stdout 仍是合法可解析 JSON
    (内层引号被正确转义), 解析两层后拿回原内容。

脚本逻辑:
  每个测试建临时目录放假 contextos stub + chmod +x, 设 PATH/env 调脚本, 断言
  退出码 + 解析 stdout JSON。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / "scripts" / "contextos-health-sessionstart.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="hook 脚本是纯 bash(POSIX 专属机制), Windows 阶段2 skipif(spec 附录C)",
)

# 脚本级超时依赖 coreutils timeout/gtimeout(本机可能没有, 如裸 macOS)。无则脚本裸跑、
# 不超时, test_timeout_fail_open 走不通, 故下面用此 guard skip 该测(见 README §5)。
_HAS_TIMEOUT = (
    shutil.which("timeout") is not None or shutil.which("gtimeout") is not None
)


def _write_stub(tmp_path: Path, body: str) -> Path:
    """在 tmp_path 放一个名为 contextos 的可执行 stub, 返回其所在目录(供拼 PATH)。"""
    binstub = tmp_path / "contextos"
    binstub.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    binstub.chmod(0o755)
    return tmp_path


def _run(env_path_dir: Path | None, extra_env: dict | None = None,
         timeout: int = 30) -> subprocess.CompletedProcess:
    # 关键: 绝不改 PATH —— subprocess.run(["bash", ...]) 自身要靠 PATH 找到 bash,
    # 把 PATH 改成 /nonexistent 会让 subprocess 直接抛 FileNotFoundError(连脚本都跑不起来)。
    # 命令缺失分支改由 CONTEXTOS_BIN 指向不存在的命令触发(见 test_missing_command_fail_open)。
    env = dict(os.environ)
    if env_path_dir is not None:
        env["PATH"] = f"{env_path_dir}:{env['PATH']}"
    env.setdefault("CONTEXTOS_HEALTH_TIMEOUT", "5")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOK)], capture_output=True, text=True,
        env=env, timeout=timeout)


def _parse_outer(stdout: str) -> dict:
    """解析脚本输出的外层 SessionStart hook JSON。"""
    return json.loads(stdout.strip())


def test_hook_script_exists_and_executable():
    assert HOOK.exists(), f"hook 脚本不存在: {HOOK}"
    assert os.access(HOOK, os.X_OK), "hook 脚本缺可执行位"


def test_success_injects_health_json():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        bindir = _write_stub(td, "echo '{\"health\":{\"engine\":\"ok\"},\"profile_info\":{\"data_dir\":\"/x\"}}'\n")
        res = _run(bindir)
        assert res.returncode == 0, res.stderr
        outer = _parse_outer(res.stdout)
        assert outer["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        inner = json.loads(outer["hookSpecificOutput"]["additionalContext"])
        assert inner["health"]["engine"] == "ok"
        assert inner["profile_info"]["data_dir"] == "/x"


def test_missing_command_fail_open():
    # 用 CONTEXTOS_BIN 指向不存在的命令触发脚本 `command -v` 缺失分支(不动 PATH —— 改 PATH
    # 会让 subprocess 连 bash 都找不到, 抛 FileNotFoundError)。env_path_dir=None 保留真实 PATH。
    res = _run(None, extra_env={"CONTEXTOS_BIN": "contextos-nope-不存在"})
    assert res.returncode == 0, res.stderr
    outer = _parse_outer(res.stdout)
    ctx = outer["hookSpecificOutput"]["additionalContext"]
    assert "探活不可用" in ctx
    assert "未找到" in ctx


def test_nonzero_exit_fail_open():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        bindir = _write_stub(Path(d), "exit 1\n")
        res = _run(bindir)
        assert res.returncode == 0, res.stderr
        ctx = _parse_outer(res.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "非零退出" in ctx


def test_empty_output_fail_open():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        bindir = _write_stub(Path(d), "exit 0\n")  # 啥都不吐
        res = _run(bindir)
        assert res.returncode == 0, res.stderr
        ctx = _parse_outer(res.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "为空" in ctx


@pytest.mark.skipif(
    not _HAS_TIMEOUT,
    reason="无 coreutils timeout/gtimeout: 脚本退化为裸跑、不超时, 此测不适用(README §5)",
)
def test_timeout_fail_open():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        bindir = _write_stub(Path(d), "sleep 10\necho late\n")
        res = _run(bindir, extra_env={"CONTEXTOS_HEALTH_TIMEOUT": "1"})
        assert res.returncode == 0, res.stderr
        ctx = _parse_outer(res.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "超时" in ctx


def test_quotes_escaped_in_injection():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        # 吐含双引号的 JSON; 脚本必须转义使外层仍可解析
        bindir = _write_stub(Path(d), "echo '{\"k\":\"v with \\\"q\\\" inside\"}'\n")
        res = _run(bindir)
        assert res.returncode == 0, res.stderr
        outer = _parse_outer(res.stdout)  # 外层可解析 = 转义正确
        inner = json.loads(outer["hookSpecificOutput"]["additionalContext"])
        assert inner["k"] == 'v with "q" inside'
