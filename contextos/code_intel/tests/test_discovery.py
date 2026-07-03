"""discovery.discover_vscode_jdtls + discover_runtime_bundle / validate_jdtls_layout 单测。

设计思路: 用 tmp_path 造假目录树, 两组探测各测各的坑 ——
(1) VSCode 扩展扫描: 平台后缀目录名 / jre 下钻一层版本目录 / lombok jar 文件名带版本;
    外加多版本挑最高、残缺目录跳过降级、无扩展返回 None。
(2) runtime bundle(spec C1/C4): <锚>/runtime/contextos-runtime 四件套深校验 ——
    launcher jar / 平台 config 目录 / lombok.jar / jre/bin/java / java-indexer.jar
    任一缺 = None(不是浅 exists 假 ok); 命中输出全绝对路径(spec C2: 不以 runtime/
    开头, 相对路径会被 validate_profile 拒 + indexer chokepoint 挂 cwd 漂移)。
    config 目录名走 platform_config 显式注入(合成树固定 config_mac_arm, CI 平台无关);
    java 可执行名跟随 sys.platform(实现按当前 OS 找 java/java.exe, Windows 真机也要绿)。
评分标准: 命中路径落在期望目录且全部 as_posix 形(要贴进 TOML, Windows 反斜杠是转义
炸弹); 四件套每件都有独立负向用例。脚本逻辑: 每测建树 -> 调 discover -> 断言。
"""
from __future__ import annotations

import sys
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


# --------------------------------------------------------------------- runtime bundle(spec C1/C4)


def _mk_runtime(root: Path, platform_config: str = "config_mac_arm", *,
                with_indexer: bool = True, with_lombok: bool = True,
                with_java: bool = True, break_launcher: bool = False) -> Path:
    """合成 <root>/runtime/contextos-runtime 布局(深校验四件套 + launcher/config)。

    config 目录名由 platform_config 注入(测试全用 config_mac_arm, CI 在 linux 跑也
    确定性); java 可执行名按当前 sys.platform 分派 —— 实现按当前 OS 找 java/java.exe,
    合成树必须跟实现同款分派, Windows 真机跑测试才不假红。
    """
    rt = root / "runtime" / "contextos-runtime"
    (rt / "jdtls" / "plugins").mkdir(parents=True)
    if not break_launcher:
        (rt / "jdtls" / "plugins" / "org.eclipse.equinox.launcher_1.7.0.jar").write_bytes(b"x")
    (rt / "jdtls" / platform_config).mkdir()
    (rt / "jre" / "bin").mkdir(parents=True)
    if with_java:
        java = rt / "jre" / "bin" / ("java.exe" if sys.platform == "win32" else "java")
        java.write_bytes(b"x")
    if with_lombok:
        (rt / "lombok.jar").write_bytes(b"x")
    if with_indexer:
        (rt / "java-indexer.jar").write_bytes(b"x")
    return rt


def test_discover_runtime_bundle_full_kit(tmp_path, monkeypatch):
    rt = _mk_runtime(tmp_path)
    monkeypatch.chdir(tmp_path)   # 探测锚 = cwd(与 indexer_jar chokepoint 同约定)
    from contextos.code_intel.jdtls_provider.discovery import discover_runtime_bundle
    found = discover_runtime_bundle(platform_config="config_mac_arm")   # 显式注入=平台无关确定性
    assert found is not None
    # 全绝对路径(spec C2/C3: 不以 runtime/ 开头, 且 as_posix 形无反斜杠)
    for p in (found.jdtls_path, found.lombok_path, found.java_home, found.indexer_jar):
        assert Path(p).is_absolute() and not p.startswith("runtime/")
        assert "\\" not in p
    assert found.jdtls_path == (rt / "jdtls").resolve().as_posix()
    assert found.lombok_path == (rt / "lombok.jar").resolve().as_posix()
    assert found.java_home == (rt / "jre").resolve().as_posix()
    assert found.indexer_jar == (rt / "java-indexer.jar").resolve().as_posix()
    assert found.source == "runtime-bundle"


def test_discover_runtime_bundle_repo_param_overrides_cwd(tmp_path):
    """repo= 显式锚(不 chdir): 供调用方注入仓根, 测试也不依赖进程 cwd。"""
    _mk_runtime(tmp_path)
    from contextos.code_intel.jdtls_provider.discovery import discover_runtime_bundle
    found = discover_runtime_bundle(repo=tmp_path, platform_config="config_mac_arm")
    assert found is not None and found.source == "runtime-bundle"


def test_discover_runtime_bundle_missing_launcher_is_none(tmp_path, monkeypatch):
    """深校验: 缺 launcher jar = 未命中(不是浅 exists 假 ok)。"""
    _mk_runtime(tmp_path, break_launcher=True)
    monkeypatch.chdir(tmp_path)
    from contextos.code_intel.jdtls_provider.discovery import discover_runtime_bundle
    assert discover_runtime_bundle(platform_config="config_mac_arm") is None


def test_discover_runtime_bundle_missing_indexer_is_none(tmp_path, monkeypatch):
    """四件套: 缺 java-indexer.jar 也算未命中(spec C1)。"""
    _mk_runtime(tmp_path, with_indexer=False)
    monkeypatch.chdir(tmp_path)
    from contextos.code_intel.jdtls_provider.discovery import discover_runtime_bundle
    assert discover_runtime_bundle(platform_config="config_mac_arm") is None


def test_discover_runtime_bundle_missing_lombok_is_none(tmp_path, monkeypatch):
    """四件套: 缺 lombok.jar = 未命中。"""
    _mk_runtime(tmp_path, with_lombok=False)
    monkeypatch.chdir(tmp_path)
    from contextos.code_intel.jdtls_provider.discovery import discover_runtime_bundle
    assert discover_runtime_bundle(platform_config="config_mac_arm") is None


def test_discover_runtime_bundle_missing_java_is_none(tmp_path, monkeypatch):
    """四件套: 缺 jre/bin/java(当前平台可执行名)= 未命中。"""
    _mk_runtime(tmp_path, with_java=False)
    monkeypatch.chdir(tmp_path)
    from contextos.code_intel.jdtls_provider.discovery import discover_runtime_bundle
    assert discover_runtime_bundle(platform_config="config_mac_arm") is None


def test_discover_runtime_bundle_wrong_platform_config_is_none(tmp_path, monkeypatch):
    """深校验: bundle 是别的平台的(config 目录不匹配)= 未命中 —— 治"下错平台包假 ok"。"""
    _mk_runtime(tmp_path, platform_config="config_mac_arm")
    monkeypatch.chdir(tmp_path)
    from contextos.code_intel.jdtls_provider.discovery import discover_runtime_bundle
    assert discover_runtime_bundle(platform_config="config_win") is None


def test_discover_runtime_bundle_no_runtime_dir_is_none(tmp_path, monkeypatch):
    """无 runtime/ 目录 -> None(最常见路径, 不该抛)。"""
    monkeypatch.chdir(tmp_path)
    from contextos.code_intel.jdtls_provider.discovery import discover_runtime_bundle
    assert discover_runtime_bundle(platform_config="config_mac_arm") is None


# --------------------------------------------------------------------- validate_jdtls_layout(spec C4)


def test_validate_jdtls_layout_pass(tmp_path):
    rt = _mk_runtime(tmp_path)
    from contextos.code_intel.jdtls_provider.discovery import validate_jdtls_layout
    assert validate_jdtls_layout(rt / "jdtls", platform_config="config_mac_arm") is None


def test_validate_jdtls_layout_missing_launcher_reason(tmp_path):
    rt = _mk_runtime(tmp_path, break_launcher=True)
    from contextos.code_intel.jdtls_provider.discovery import validate_jdtls_layout
    reason = validate_jdtls_layout(rt / "jdtls", platform_config="config_mac_arm")
    assert reason is not None and "launcher" in reason


def test_validate_jdtls_layout_missing_platform_config_reason(tmp_path):
    rt = _mk_runtime(tmp_path, platform_config="config_mac_arm")
    from contextos.code_intel.jdtls_provider.discovery import validate_jdtls_layout
    reason = validate_jdtls_layout(rt / "jdtls", platform_config="config_win")
    assert reason is not None and "config_win" in reason


def test_current_platform_config_maps_to_ssot_value():
    """_current_platform_config 的 key 词形必须与 SSOT 字典真实 key 对齐 ——
    任何当前平台都要能查到一个 config_* 目录名(KeyError = 映射漂移)。"""
    from contextos.code_intel.jdtls_provider.discovery import _current_platform_config
    from contextos.code_intel.jdtls_provider.solidlsp.language_servers.eclipse_jdtls import (
        JDTLS_CONFIG_DIR_BY_PLATFORM,
    )
    assert _current_platform_config() in set(JDTLS_CONFIG_DIR_BY_PLATFORM.values())
