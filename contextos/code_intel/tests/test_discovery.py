"""discovery.discover_vscode_jdtls 单测。

设计思路: 用 tmp_path 造假 VSCode 扩展目录树, 覆盖我们真踩过的三个定位坑 ——
平台后缀目录名 / jre 下钻一层版本目录 / lombok jar 文件名带版本; 外加多版本挑最高、
残缺目录跳过降级、无扩展返回 None。评分标准: 三路径命中期望目录且全部 as_posix 形
(要贴进 TOML, Windows 反斜杠是转义炸弹)。脚本逻辑: 每测建树 -> 调 discover -> 断言。
"""
from __future__ import annotations

from pathlib import Path

from contextos.code_intel.jdtls_provider.discovery import discover_vscode_jdtls


def _make_ext(home: Path, name: str, *, with_server: bool = True,
              lombok_name: str = "lombok-1.18.39.jar",
              jre_sub: str | None = "21.0.10-macosx-aarch64",
              java_name: str = "java") -> Path:
    ext = home / ".vscode" / "extensions" / name
    if with_server:
        (ext / "server").mkdir(parents=True)
    if lombok_name:
        (ext / "lombok").mkdir(parents=True, exist_ok=True)
        (ext / "lombok" / lombok_name).write_bytes(b"PK")
    if jre_sub is not None:
        jre_home = ext / "jre" / jre_sub if jre_sub else ext / "jre"
        (jre_home / "bin").mkdir(parents=True)
        (jre_home / "bin" / java_name).write_bytes(b"#!")
    return ext


def test_discover_happy_path_all_three_as_posix(tmp_path):
    ext = _make_ext(tmp_path, "redhat.java-1.53.0-darwin-arm64")
    found = discover_vscode_jdtls(home=tmp_path)
    assert found is not None
    assert found.jdtls_path == (ext / "server").as_posix()
    assert found.lombok_path == (ext / "lombok" / "lombok-1.18.39.jar").as_posix()
    assert found.java_home == (ext / "jre" / "21.0.10-macosx-aarch64").as_posix()
    assert found.source == "redhat.java-1.53.0-darwin-arm64"
    assert "\\" not in found.jdtls_path + found.lombok_path + found.java_home


def test_discover_picks_highest_version(tmp_path):
    _make_ext(tmp_path, "redhat.java-1.52.0-darwin-arm64")
    _make_ext(tmp_path, "redhat.java-1.53.0-darwin-arm64")
    found = discover_vscode_jdtls(home=tmp_path)
    assert found is not None and found.source == "redhat.java-1.53.0-darwin-arm64"


def test_discover_skips_broken_highest_falls_back(tmp_path):
    """最高版本目录残缺(没 server/)-> 跳过用次高, 不是硬 None。"""
    _make_ext(tmp_path, "redhat.java-1.52.0-darwin-arm64")
    _make_ext(tmp_path, "redhat.java-1.53.0-darwin-arm64", with_server=False)
    found = discover_vscode_jdtls(home=tmp_path)
    assert found is not None and found.source == "redhat.java-1.52.0-darwin-arm64"


def test_discover_jre_direct_layout(tmp_path):
    """个别布局 jre/ 自己就是 home(bin/java 直挂)-> 认 jre/ 本身。"""
    _make_ext(tmp_path, "redhat.java-1.53.0-linux-x64", jre_sub="")
    found = discover_vscode_jdtls(home=tmp_path)
    assert found is not None and found.java_home.endswith("/jre")


def test_discover_windows_java_exe(tmp_path):
    """Windows 形状: jre 版本层下是 bin/java.exe(mac 上造同形树即可验此判定)。"""
    ext = _make_ext(tmp_path, "redhat.java-1.53.0-win32-x64",
                    jre_sub="21.0.10-win32-x86_64", java_name="java.exe")
    found = discover_vscode_jdtls(home=tmp_path)
    assert found is not None
    assert found.java_home == (ext / "jre" / "21.0.10-win32-x86_64").as_posix()


def test_discover_none_when_no_extensions_dir(tmp_path):
    assert discover_vscode_jdtls(home=tmp_path) is None


def test_discover_none_when_jre_missing_java(tmp_path):
    ext = _make_ext(tmp_path, "redhat.java-1.53.0-darwin-arm64", jre_sub=None)
    (ext / "jre" / "21.0.10-macosx-aarch64").mkdir(parents=True)   # 有目录没 bin/java
    assert discover_vscode_jdtls(home=tmp_path) is None


def test_discover_skips_ext_missing_lombok(tmp_path):
    """m6 mutation 补坑(冷验证抓出): 三件齐全判定的 lombok 腿必须有守卫 —— 缺 lombok jar
    的扩展整个跳过(有次高完整版本则降级, 没有则 None); 该腿被回归删除时 lomboks[-1]
    会 IndexError, 此测试必须红。"""
    _make_ext(tmp_path, "redhat.java-1.53.0-darwin-arm64", lombok_name="")
    assert discover_vscode_jdtls(home=tmp_path) is None
    _make_ext(tmp_path, "redhat.java-1.52.0-darwin-arm64")
    found = discover_vscode_jdtls(home=tmp_path)
    assert found is not None and found.source == "redhat.java-1.52.0-darwin-arm64"
