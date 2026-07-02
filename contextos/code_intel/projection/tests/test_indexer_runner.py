"""indexer_runner: 命令拼装(java_home/xmx/--files) / jar 缺失给重建指引 /
非零退出抛 IndexerError 带 stderr 尾部 / 跑前清 stale JSONL。
subprocess 用 fake 脚本替真 jar(离线单测)。"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from contextos.code_intel.projection.indexer_runner import (
    IndexerError, build_command, run_indexer,
)


def _fake_java(tmp_path: Path, script: str) -> Path:
    fake = tmp_path / "fakejava"
    fake.write_text(f"#!/bin/sh\n{script}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def test_build_command_full(tmp_path):
    cmd = build_command(java_home="/jdk", jar=tmp_path / "x.jar", xmx="4g",
                        ctx_file=tmp_path / "ctx.json", out_dir=tmp_path / "out",
                        files_list=None)
    if sys.platform == "win32":
        # Windows: 原生分隔符 + .exe(阶段3 java.exe 平台分派)
        assert cmd[0] == str(Path("/jdk") / "bin" / "java.exe")
    else:
        assert cmd[0] == "/jdk/bin/java"    # POSIX 钉死原字面, 逐字零回归锚
    assert "-Xmx4g" in cmd
    assert "--files" not in cmd


def test_build_command_win32_appends_exe(tmp_path, monkeypatch):
    """win32 分支规则在 mac/Linux 上可证(同 Phase2 PureWindowsPath 手法):
    monkeypatch sys.platform -> build_command 必须拼 bin/java.exe 且仍走 expanduser。
    真 Windows 上该分支再由 test_build_command_full 原生复验。"""
    monkeypatch.setattr(sys, "platform", "win32")   # indexer_runner 调用期读 sys.platform
    cmd = build_command(java_home="~/jdk", jar=tmp_path / "x.jar", xmx="1g",
                        ctx_file=tmp_path / "ctx.json", out_dir=tmp_path / "out",
                        files_list=None)
    assert cmd[0].endswith("java.exe")
    assert not cmd[0].startswith("~")        # expanduser 在 win32 分支同样生效
    assert cmd[0] == str(Path("~/jdk").expanduser() / "bin" / "java.exe")


def test_build_command_subset(tmp_path):
    lst = tmp_path / "only.txt"
    cmd = build_command(java_home="", jar=tmp_path / "x.jar", xmx="2g",
                        ctx_file=tmp_path / "ctx.json", out_dir=tmp_path / "out",
                        files_list=lst)
    assert cmd[0] == "java"                     # 空 java_home -> PATH 上的 java
    assert cmd[-2:] == ["--files", str(lst)]


def test_missing_jar_raises_with_rebuild_hint(tmp_path):
    with pytest.raises(IndexerError, match="vendor/java-indexer/README.md"):
        run_indexer(java_home="", jar=tmp_path / "missing.jar", xmx="1g",
                    ctx_file=tmp_path / "ctx.json", out_dir=tmp_path / "out")


@pytest.mark.skipif(sys.platform == "win32",
                    reason="fake java 用 #!/bin/sh(POSIX 专属可执行机制), 阶段3才连真机 java.exe(spec 附录C)")
def test_nonzero_exit_raises(tmp_path):
    fake = _fake_java(tmp_path, "echo boom >&2\nexit 3")
    jar = tmp_path / "x.jar"
    jar.write_bytes(b"PK")
    with pytest.raises(IndexerError, match="boom"):
        run_indexer(java_home="", jar=jar, xmx="1g", ctx_file=tmp_path / "ctx.json",
                    out_dir=tmp_path / "out", _java_override=str(fake))


@pytest.mark.skipif(sys.platform == "win32",
                    reason="fake java 用 #!/bin/sh(POSIX 专属可执行机制), 阶段3才连真机 java.exe(spec 附录C)")
def test_stale_jsonl_cleaned_before_run(tmp_path):
    """第三轮 review MEDIUM: out_dir 复用时, 上次残留 JSONL 必须在 jar 跑前被清,
    否则 loader 把旧行当本次结果。"""
    fake = _fake_java(tmp_path, "exit 0")     # 本次 jar 不产任何文件
    jar = tmp_path / "x.jar"
    jar.write_bytes(b"PK")
    out = tmp_path / "out"
    out.mkdir()
    (out / "classes.jsonl").write_text('{"classFqn":"Stale","className":"Stale"}')
    run_indexer(java_home="", jar=jar, xmx="1g", ctx_file=tmp_path / "ctx.json",
                out_dir=out, _java_override=str(fake))
    assert not (out / "classes.jsonl").exists()   # 残留已清, loader 读不到 Stale


def test_jar_fingerprint(tmp_path):
    from contextos.code_intel.projection.indexer_runner import jar_fingerprint
    jar = tmp_path / "x.jar"
    jar.write_bytes(b"PK123")
    fp1 = jar_fingerprint(jar)
    jar.write_bytes(b"PK124")
    assert jar_fingerprint(jar) != fp1
    assert len(fp1) == 40


def test_build_command_expands_tilde_java_home(tmp_path):
    """真某电信客户项目冒烟实锤: profile java_home 写 ~/... 时 subprocess 不展开 ~ -> 必须在此展开。
    (Windows ntpath.expanduser 读 USERPROFILE, 同样适用。)"""
    from pathlib import Path as _P
    cmd = build_command(java_home="~/jdk", jar=tmp_path / "x.jar", xmx="1g",
                        ctx_file=tmp_path / "ctx.json", out_dir=tmp_path / "out",
                        files_list=None)
    assert not cmd[0].startswith("~")
    if sys.platform == "win32":
        assert cmd[0] == str(_P("~/jdk").expanduser() / "bin" / "java.exe")
    else:
        assert cmd[0] == f"{_P('~/jdk').expanduser()}/bin/java"   # POSIX 原字面锚


@pytest.mark.cmd_boundary
def test_run_indexer_stderr_decoded_not_bytes_repr(tmp_path, monkeypatch):
    """站点9: java 非 0 退出, stderr 含非 ASCII -> IndexerError 文本是解码后字符串(decode_diagnostic),
    不是 b'...' repr; 不崩(bytes-mode 切片 + utf-8 replace)。"""
    import subprocess as _sp

    from contextos.code_intel.projection import indexer_runner as IR

    jar = tmp_path / "x.jar"
    jar.write_bytes(b"PK")

    def fake_run(cmd, **kw):
        assert "text" not in kw          # bytes 模式(不再 text=True)
        return _sp.CompletedProcess(cmd, 2, stdout=b"", stderr="编译失败 错误".encode("utf-8"))

    monkeypatch.setattr(IR.subprocess, "run", fake_run)
    try:
        IR.run_indexer(java_home="", jar=jar, xmx="1g",
                       ctx_file=tmp_path / "c.json", out_dir=tmp_path / "out",
                       _java_override="/bin/true")
        assert False, "应抛 IndexerError"
    except IR.IndexerError as exc:
        msg = str(exc)
        assert "编译失败" in msg          # 解码后中文可读
        assert "b'" not in msg and "\\xe7" not in msg   # 非 bytes repr / 非转义
