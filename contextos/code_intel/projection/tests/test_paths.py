"""路径口径: projects[0] / source_roots 空=整仓 / 相对根挂 repo 绝对根原样 / 全 resolved。"""
from __future__ import annotations

import sys

import pytest

from contextos.code_intel.projection.paths import (
    primary_project,
    repo_root,
    resolve_source_roots,
)


def test_primary_project_and_repo_root(make_profile, tmp_path):
    repo = (tmp_path / "repo")
    repo.mkdir()
    prof = make_profile(project_path=str(repo))
    assert primary_project(prof).path == str(repo)
    assert repo_root(prof) == repo.resolve()


def test_source_roots_empty_means_whole_repo(make_profile, tmp_path):
    repo = (tmp_path / "repo")
    repo.mkdir()
    prof = make_profile(project_path=str(repo), source_roots=[])
    assert resolve_source_roots(prof) == [repo.resolve()]


def test_source_roots_relative_and_absolute(make_profile, tmp_path):
    repo = (tmp_path / "repo")
    (repo / "src/main/java").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    prof = make_profile(project_path=str(repo),
                        source_roots=["src/main/java", str(other)])
    assert resolve_source_roots(prof) == [(repo / "src/main/java").resolve(),
                                          other.resolve()]


@pytest.mark.skipif(sys.platform == "win32",
                    reason="symlink 创建默认需管理员权限(POSIX 权限模型), spec 附录C")
def test_symlinked_repo_root_resolved(make_profile, tmp_path):
    """F4: /tmp vs /private/tmp 同款——symlink 进来的 repo_root 必须 resolve 掉。"""
    real = tmp_path / "real-repo"
    real.mkdir()
    link = tmp_path / "link-repo"
    link.symlink_to(real)
    prof = make_profile(project_path=str(link))
    assert repo_root(prof) == real.resolve()
    assert resolve_source_roots(prof) == [real.resolve()]


def test_indexer_jar_chokepoint(make_profile, tmp_path, monkeypatch):
    """NIT-1(最终 review): jar 路径解析收 chokepoint —— 绝对原样, 相对挂 cwd
    (仓根约定, 同 rebuild_entry / init 旧手写口径), 消两处内联漂移源。

    第一断言在 chdir(tmp_path) 之前跑, 此时 cwd 仍是 pytest 的启动目录 ——
    若该目录恰好带 <cwd>/runtime/contextos-runtime(本仓根即是, spec A11
    resolve_effective_runtime 的 bundle 回退支路), indexer_jar 会被 bundle
    命中的 jar 抢先, 而不是断言期望的配置 abs 路径。钉死 discover_runtime_bundle
    返回 None 使两条断言都只验 chokepoint 本身(绝对原样 / 相对挂 cwd), 与
    bundle 探测支路解耦(hermetic, 同 test_health_jdtls_probe.py 的先例)。"""
    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(D, "discover_runtime_bundle",
                        lambda repo=None, platform_config=None: None)

    from contextos.code_intel.projection.paths import indexer_jar
    prof = make_profile()
    prof.code_index.indexer_jar = str(tmp_path / "abs" / "a.jar")
    assert indexer_jar(prof) == tmp_path / "abs" / "a.jar"
    prof.code_index.indexer_jar = "vendor/x.jar"
    monkeypatch.chdir(tmp_path)
    from pathlib import Path as _P
    assert indexer_jar(prof) == _P.cwd() / "vendor" / "x.jar"


def test_indexer_jar_falls_back_to_bundle(tmp_path, monkeypatch):
    from contextos.code_intel.projection.paths import indexer_jar
    # 造 bundle(布局同 test_discovery._mk_bundle, 内联避免跨测试文件 import)
    rt = tmp_path / "runtime" / "contextos-runtime"
    (rt / "jdtls" / "plugins").mkdir(parents=True)
    (rt / "jdtls" / "plugins" / "org.eclipse.equinox.launcher_1.jar").write_bytes(b"x")
    (rt / "jdtls" / "config_test").mkdir()
    (rt / "jre" / "bin").mkdir(parents=True)
    import sys
    (rt / "jre" / "bin" / ("java.exe" if sys.platform == "win32" else "java")).write_bytes(b"x")
    (rt / "lombok.jar").write_bytes(b"x")
    (rt / "java-indexer.jar").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "contextos.code_intel.jdtls_provider.discovery._current_platform_config",
        lambda: "config_test")

    class _P:  # 最小 duck profile
        class jdtls_runtime:
            jdtls_path, lombok_path, java_home = "/nx/a", "/nx/b", "/nx/c"
        class code_index:
            indexer_jar = "vendor/java-indexer/target/java-indexer-1.0.0.jar"
    assert indexer_jar(_P()) == rt / "java-indexer.jar"


def test_indexer_jar_unverified_keeps_cwd_join(tmp_path, monkeypatch):
    """无 bundle 且配置 jar 不存在时: 相对路径仍挂 cwd 解析(旧报错口径不变)。"""
    from contextos.code_intel.projection.paths import indexer_jar
    monkeypatch.chdir(tmp_path)

    class _P:
        class jdtls_runtime:
            jdtls_path, lombok_path, java_home = "/nx/a", "/nx/b", "/nx/c"
        class code_index:
            indexer_jar = "vendor/nope.jar"
    assert indexer_jar(_P()) == tmp_path / "vendor" / "nope.jar"
