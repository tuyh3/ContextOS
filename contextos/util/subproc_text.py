"""subprocess 命令边界硬化(三平台): rg/git 受控调用 + bytes 解码 helper。

设计见 docs/superpowers/specs/2026-06-30-subprocess命令边界硬化-design.md。
- run_rg/run_git: 绝对可执行 + argv list(shell=False)+ 固定确定性 flags + capture bytes。
- decode_content/decode_diagnostic: 内容/诊断字段 utf-8 + replace(防杂散字节崩 / bytes repr 退化诊断)。
- 路径字段由调用点直接 os.fsdecode(path_b)(bytes 模式 Unix=surrogateescape / Win=surrogatepass)。
helper 只收边界共性; per-call 的 --null/-z/-n、returncode 判定、切分与解码留调用点(回归面小)。
"""
from __future__ import annotations

import shutil
import subprocess

# 解析一次绝对路径防 PATH 歧义; 缺则裸名(调用点已各自 shutil.which 探测兜底)。
_RG = shutil.which("rg") or "rg"
_GIT = shutil.which("git") or "git"


def run_rg(args: list[str], *, cwd: str | None = None,
           timeout: float | None = None) -> subprocess.CompletedProcess:
    """程序化 rg: 恒注入 --no-config(屏蔽 RIPGREP_CONFIG_PATH 注入)--color=never(防强制上色),
    capture bytes(无 text=), shell=False(argv list)。args 不含 'rg'。
    --no-config 前置(load-time 开关、不可被覆盖); --color=never 尾置以压制调用点意外传入的
    --color(rg 后置者赢: 末尾 --color=never 恒压住 args 里任何 --color=always)。"""
    return subprocess.run(
        [_RG, "--no-config", *args, "--color=never"],
        capture_output=True, cwd=cwd, timeout=timeout)


def run_git(args: list[str], *, cwd: str | None = None,
            timeout: float | None = None) -> subprocess.CompletedProcess:
    """程序化 git: capture bytes, shell=False。args 不含 'git'。
    git 无 rg 式全局配置注入; 机器格式靠调用点显式 -z(中和 core.quotePath)。"""
    return subprocess.run(
        [_GIT, *args], capture_output=True, cwd=cwd, timeout=timeout)


def decode_content(b: bytes) -> str:
    """内容字段(源/语料 UTF-8): replace 防杂散字节崩。"""
    return b.decode("utf-8", errors="replace")


def decode_diagnostic(b: bytes) -> str:
    """rg/git 的 error/warning/stderr 尾(bytes-mode 后 stderr 也是 bytes): 防 b'...' repr 退化诊断。"""
    return b.decode("utf-8", errors="replace")
