"""subproc_text helper 单测。

设计思路: helper 是命令边界唯一收口点 —— 测它(a)run_rg 恒注入 --no-config --color=never
(b)capture bytes(无 text=)(c)--no-config 真防 RIPGREP_CONFIG_PATH 注入(d)decode_* 函数
utf-8+replace 不崩(e)os.fsdecode 本地 FS roundtrip 不变量(POSIX argv 走 os.fsencode)
(f)解码 locale 无关(LC_ALL=C 子进程与默认解析字节一致)。
评分标准: 每条对应 spec §7.1/§7.3a/§7.5/§7.6 一项; rg 真跑(缺 rg 则 skip)。
脚本逻辑: spy subprocess.run 验 argv/flags(不跑真进程); 真 rg 子进程验注入防护与 locale 一致。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from contextos.util.subproc_text import (
    decode_content,
    decode_diagnostic,
    run_git,
    run_rg,
)

_HAS_RG = shutil.which("rg") is not None

# 本文件全为命令边界测试 -> 模块级 marker(本地 -m cmd_boundary 选此跨平台子集;
# Windows CI 阶段2 起已跑全量 not-integration, 不再靠此 marker 选子集)
pytestmark = pytest.mark.cmd_boundary


def test_run_rg_injects_flags_and_captures_bytes(monkeypatch):
    """run_rg 恒带 --no-config --color=never; capture_output=True; 无 text/encoding; argv 透传。"""
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_rg(["-n", "-e", "x", "."], cwd="/tmp", timeout=5)
    cmd = captured["cmd"]
    assert cmd[0].lower() == "rg" or cmd[0].lower().endswith(("/rg", "\\rg", "rg.exe"))
    assert "--no-config" in cmd and "--color=never" in cmd
    assert cmd[1] == "--no-config"                       # --no-config 紧跟 rg(load-time, 不可覆盖)
    assert cmd[-1] == "--color=never"                    # --color=never 末尾, 压住调用点任何 --color
    assert cmd[2:-1] == ["-n", "-e", "x", "."]           # 调用点 args 夹在中间
    assert captured["kw"]["capture_output"] is True
    assert "text" not in captured["kw"] and "encoding" not in captured["kw"]
    assert captured["kw"]["cwd"] == "/tmp" and captured["kw"]["timeout"] == 5


def test_run_git_argv_and_bytes(monkeypatch):
    """run_git: argv list(无 shell), capture bytes, 无 text。"""
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_git(["rev-parse", "HEAD"], timeout=10)
    cmd = captured["cmd"]
    assert cmd[0].lower() == "git" or cmd[0].lower().endswith(("/git", "\\git", "git.exe"))
    assert cmd[1:] == ["rev-parse", "HEAD"]
    assert captured["kw"]["capture_output"] is True
    assert "text" not in captured["kw"] and "encoding" not in captured["kw"]


def test_decode_content_utf8_and_replace():
    assert decode_content("中文 NEEDLE".encode("utf-8")) == "中文 NEEDLE"
    assert decode_content(b"a\xff\xfeb") == "a��b"   # 杂散字节 replace, 不崩


def test_decode_diagnostic_utf8_replace():
    assert decode_diagnostic(b"git error \xff tail") == "git error � tail"


def test_fsdecode_local_roundtrip(tmp_path):
    """§7.3a: 本地 FS roundtrip os.fsdecode(os.fsencode(str(p))) == str(p)。
    只证本地 roundtrip(POSIX argv 走 os.fsencode, rg 回吐同字节);
    不证 Windows rg stdout(§7.3b 真 rg smoke 兜, 在 test_source_search.py)。"""
    p = tmp_path / "中文name.java"
    p.write_text("x", encoding="utf-8")
    assert os.fsdecode(os.fsencode(str(p))) == str(p)


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_run_rg_no_config_defeats_injection(tmp_path, monkeypatch):
    """§7.6: RIPGREP_CONFIG_PATH 含 --max-count=1 + --color=always 时, run_rg(--no-config)
    在**同一文件 2 命中行**仍返回 2 条(--max-count 按文件: 2 文件各 1 命中漏测), 且 stdout 无 ANSI。"""
    rc = tmp_path / "ripgreprc"
    rc.write_text("--max-count=1\n--color=always\n", encoding="utf-8")
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(rc))
    f = tmp_path / "two.txt"
    f.write_text("NEEDLE one\nNEEDLE two\n", encoding="utf-8")    # 同文件 2 命中
    proc = run_rg(["-n", "-F", "-e", "NEEDLE", str(f)])
    lines = [ln for ln in proc.stdout.split(b"\n") if ln]
    assert len(lines) == 2                       # --max-count 被屏蔽 -> 2 条(不是 1)
    assert b"\x1b[" not in proc.stdout           # --color=always 被屏蔽 -> 无 ANSI


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_run_rg_color_never_dominates_call_site_color(tmp_path):
    """调用点即便误传 --color=always, helper 尾置 --color=never 仍压住(无 ANSI)。"""
    f = tmp_path / "x.txt"
    f.write_text("NEEDLE\n", encoding="utf-8")
    proc = run_rg(["-n", "--color=always", "-e", "NEEDLE", str(f)])
    assert b"\x1b[" not in proc.stdout


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_run_rg_locale_independent(tmp_path):
    """§7.5b: run_rg 解析 locale 无关 —— LC_ALL=C 子进程跑同查询, stdout 字节与默认 locale 一致
    (content 恒 utf-8 / path 恒 os.fsdecode, 不随 LC_ALL 变)。"""
    f = tmp_path / "中文.txt"
    f.write_text("配置 NEEDLE 值\n", encoding="utf-8")
    direct = run_rg(["-n", "-F", "--null", "-e", "NEEDLE", str(f)]).stdout
    script = (
        "import sys;"
        "from contextos.util.subproc_text import run_rg;"
        "sys.stdout.buffer.write(run_rg(['-n','-F','--null','-e','NEEDLE', sys.argv[1]]).stdout)"
    )
    out_c = subprocess.run(
        [sys.executable, "-c", script, str(f)],
        env={**os.environ, "LC_ALL": "C"}, capture_output=True).stdout
    assert direct == out_c and direct   # 解析与 LC_ALL 无关
