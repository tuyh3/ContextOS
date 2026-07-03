"""health_check 的 jdtls_runtime 探针测试。

设计思路: 探针契约(runtime-bundle spec C1/C4 升级后)——
- 三路径全在**且深校验过**(launcher jar + 平台 config + lombok 是文件 + java_home
  有 bin/java)-> {"status":"ok"}; 浅 exists 但内容残缺 -> missing + "(深校验)" 条目
  (治"照抄后 init 才炸"的假 ok)。
- 有缺 -> status=missing + missing 清单, 建议按 C1 顺序分岔: 先 <cwd>/runtime/
  contextos-runtime 四件套(suggestion 四行含 indexer_jar, 全绝对路径), 探不到再
  VSCode 扩展(三行), 都没有给安装指引 hint。
- profile ok 时仍单独查 code_index.indexer_jar(C1 反遮蔽): jar 缺 + bundle 在 ->
  附 indexer_jar_suggestion; jar 在或 bundle 不在 -> 保持裸 ok。
- 任何异常吞掉返回 error 串(health 绝不冒泡纪律)。
评分标准: 各分岔命中 + 深校验每条腿有独立负向用例 + 反遮蔽正反两向 + 不冒泡。
脚本逻辑: 造带 profile.jdtls_runtime 的假 app_ctx(深校验合成布局, config 目录/java
名按当前平台造 -> 三平台真机都绿); autouse 钉死 bundle 探测为 None(hermetic:
开发机真解过 bundle 不污染), 要命中的用例自行换回真函数 + tmp_path 合成树。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from contextos.code_intel.jdtls_provider.discovery import (
    DiscoveredJdtls,
    _current_platform_config,
    discover_runtime_bundle as _real_discover_runtime_bundle,
)
from contextos.mcp_server.tools.meta import _probe_jdtls_runtime

_JAVA_NAME = "java.exe" if sys.platform == "win32" else "java"


@pytest.fixture(autouse=True)
def _no_real_runtime_bundle(monkeypatch):
    """hermetic 守卫: 探针 missing 支路按 C1 先扫 <cwd>/runtime/contextos-runtime ——
    开发机若真解过 bundle 会污染分岔。钉死为"探不到"; 需要命中 bundle 的用例自行
    monkeypatch 换回真函数(树造在 tmp_path 里 + chdir)。"""
    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(D, "discover_runtime_bundle",
                        lambda repo=None, platform_config=None: None)


def _ctx(tmp_path, *, jdtls_exists=True, deep_valid=True, java_ok=True,
         lombok_is_dir=False, indexer_jar=None):
    """深校验合成布局(默认全绿): server/plugins/launcher + 平台 config + lombok 文件
    + jre/bin/java。各开关造单腿残缺形态; indexer_jar 给值才挂 code_index 命名空间。"""
    server = tmp_path / "server"
    (server / "plugins").mkdir(parents=True)
    if deep_valid:
        (server / "plugins" / "org.eclipse.equinox.launcher_1.7.0.jar").write_bytes(b"PK")
        (server / _current_platform_config()).mkdir()
    lombok = tmp_path / "lombok.jar"
    if lombok_is_dir:
        lombok.mkdir()          # 浅 exists 过、深 is_file 不过
    else:
        lombok.write_bytes(b"PK")
    jre = tmp_path / "jre"
    (jre / "bin").mkdir(parents=True)
    if java_ok:
        (jre / "bin" / _JAVA_NAME).write_bytes(b"#!")
    rt = SimpleNamespace(
        jdtls_path=str(server) if jdtls_exists else str(tmp_path / "no-such-dir"),
        lombok_path=str(lombok),
        java_home=str(jre),
    )
    profile = SimpleNamespace(jdtls_runtime=rt)
    if indexer_jar is not None:
        profile.code_index = SimpleNamespace(indexer_jar=indexer_jar)
    return SimpleNamespace(profile=profile)


def _mk_runtime_tree(root: Path) -> Path:
    """合成当前平台形状的 <root>/runtime/contextos-runtime 四件套(探针层无
    platform_config 注入口, config 目录/java 名都按当前平台造)。"""
    rt = root / "runtime" / "contextos-runtime"
    (rt / "jdtls" / "plugins").mkdir(parents=True)
    (rt / "jdtls" / "plugins" / "org.eclipse.equinox.launcher_1.7.0.jar").write_bytes(b"x")
    (rt / "jdtls" / _current_platform_config()).mkdir()
    (rt / "jre" / "bin").mkdir(parents=True)
    (rt / "jre" / "bin" / _JAVA_NAME).write_bytes(b"x")
    (rt / "lombok.jar").write_bytes(b"x")
    (rt / "java-indexer.jar").write_bytes(b"x")
    return rt


def test_probe_ok_when_all_paths_exist(tmp_path):
    assert _probe_jdtls_runtime(_ctx(tmp_path)) == {"status": "ok"}


def test_probe_missing_with_suggestion_when_vscode_found(tmp_path, monkeypatch):
    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(
        D, "discover_vscode_jdtls",
        lambda home=None: DiscoveredJdtls(
            jdtls_path="/x/server", lombok_path="/x/lombok.jar",
            java_home="/x/jre/21", source="redhat.java-9.9.9-test"),
    )
    out = _probe_jdtls_runtime(_ctx(tmp_path, jdtls_exists=False))
    assert out["status"] == "missing"
    assert out["missing"] == ["jdtls_path"]
    assert out["suggestion"]["jdtls_path"] == "/x/server"
    assert out["suggestion"]["source"] == "redhat.java-9.9.9-test"


def test_probe_missing_with_install_hint_when_no_vscode(tmp_path, monkeypatch):
    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(D, "discover_vscode_jdtls", lambda home=None: None)
    out = _probe_jdtls_runtime(_ctx(tmp_path, jdtls_exists=False))
    assert out["status"] == "missing"
    assert "suggestion" not in out
    assert "README" in out["hint"]


def test_probe_never_raises_on_broken_ctx():
    out = _probe_jdtls_runtime(SimpleNamespace())   # 没 profile 属性
    assert out["status"].startswith("error:")


# ------------------------------------------------------------- runtime bundle 支路(spec C1/C2)


def test_probe_runtime_bundle_preferred_over_vscode(tmp_path, monkeypatch):
    """spec C1 顺序: profile 三路径缺 + runtime/ 四件套在 -> suggestion 来自
    runtime-bundle(四行含 indexer_jar, 全绝对路径), 即便 VSCode 扩展也探得到。"""
    import contextos.code_intel.jdtls_provider.discovery as D
    _mk_runtime_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(D, "discover_runtime_bundle", _real_discover_runtime_bundle)
    monkeypatch.setattr(
        D, "discover_vscode_jdtls",
        lambda home=None: DiscoveredJdtls(
            jdtls_path="/x/server", lombok_path="/x/lombok.jar",
            java_home="/x/jre/21", source="redhat.java-9.9.9-test"),
    )
    out = _probe_jdtls_runtime(_ctx(tmp_path, jdtls_exists=False))
    assert out["status"] == "missing"
    sug = out["suggestion"]
    assert sug["source"] == "runtime-bundle"
    assert "indexer_jar" in sug
    for key in ("jdtls_path", "lombok_path", "java_home", "indexer_jar"):
        # spec C2: 全绝对路径, 不以 runtime/ 开头(相对路径 validate_profile 会拒)
        assert Path(sug[key]).is_absolute() and not sug[key].startswith("runtime/")
    assert "hint" in out


def test_probe_profile_ok_still_suggests_indexer(tmp_path, monkeypatch):
    """spec C1 反遮蔽: profile 三路径 ok(深校验过)但 code_index.indexer_jar 不存在
    + runtime/ 有 java-indexer.jar -> status 仍 ok 且附 indexer_jar_suggestion(绝对路径)。
    否则用 VSCode 扩展的用户永远收不到 bundle 里现成的 indexer, 继续撞"自己 mvn build"。"""
    import contextos.code_intel.jdtls_provider.discovery as D
    rt = _mk_runtime_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(D, "discover_runtime_bundle", _real_discover_runtime_bundle)
    ctx = _ctx(tmp_path, indexer_jar=str(tmp_path / "no-such-indexer.jar"))
    out = _probe_jdtls_runtime(ctx)
    assert out["status"] == "ok"
    assert Path(out["indexer_jar_suggestion"]).is_absolute()
    assert out["indexer_jar_suggestion"] == (rt / "java-indexer.jar").resolve().as_posix()


def test_probe_ok_no_indexer_suggestion_when_jar_exists(tmp_path):
    """反遮蔽的反向守卫: indexer jar 真在 -> 不给多余建议(输出保持精确 {"status":"ok"})。"""
    jar = tmp_path / "java-indexer.jar"
    jar.write_bytes(b"PK")
    out = _probe_jdtls_runtime(_ctx(tmp_path, indexer_jar=str(jar)))
    assert out == {"status": "ok"}


def test_probe_ok_indexer_missing_but_no_bundle_stays_plain_ok(tmp_path):
    """indexer jar 缺但 bundle 探不到(autouse 钉 None)-> 无从建议, 保持裸 ok 不报错。"""
    out = _probe_jdtls_runtime(_ctx(tmp_path, indexer_jar=str(tmp_path / "nope.jar")))
    assert out == {"status": "ok"}


# ------------------------------------------------------------- profile 三路径深校验(spec C4)


def test_probe_profile_ok_shallow_paths_now_fail_deep(tmp_path, monkeypatch):
    """spec C4 升级: 三路径都 exists 但 jdtls 缺 launcher/平台 config -> 不再假 ok;
    status=missing 且 missing 条目讲清缺什么(治"照抄后 init 才炸")。"""
    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(D, "discover_vscode_jdtls", lambda home=None: None)
    out = _probe_jdtls_runtime(_ctx(tmp_path, deep_valid=False))
    assert out["status"] == "missing"
    assert any("深校验" in m and "launcher" in m for m in out["missing"])


def test_probe_deep_catches_java_home_without_java(tmp_path, monkeypatch):
    """深校验 java 腿: java_home 目录在但 bin/ 下没 java 可执行 -> missing。"""
    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(D, "discover_vscode_jdtls", lambda home=None: None)
    out = _probe_jdtls_runtime(_ctx(tmp_path, java_ok=False))
    assert out["status"] == "missing"
    assert any(m.startswith("java_home(深校验)") for m in out["missing"])


def test_probe_deep_catches_lombok_dir_not_file(tmp_path, monkeypatch):
    """深校验 lombok 腿: lombok_path 指向目录(浅 exists 过)-> 深 is_file 不过。"""
    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(D, "discover_vscode_jdtls", lambda home=None: None)
    out = _probe_jdtls_runtime(_ctx(tmp_path, lombok_is_dir=True))
    assert out["status"] == "missing"
    assert any(m.startswith("lombok_path(深校验)") for m in out["missing"])
