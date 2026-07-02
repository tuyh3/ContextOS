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
    (仓根约定, 同 rebuild_entry / init 旧手写口径), 消两处内联漂移源。"""
    from contextos.code_intel.projection.paths import indexer_jar
    prof = make_profile()
    prof.code_index.indexer_jar = str(tmp_path / "abs" / "a.jar")
    assert indexer_jar(prof) == tmp_path / "abs" / "a.jar"
    prof.code_index.indexer_jar = "vendor/x.jar"
    monkeypatch.chdir(tmp_path)
    from pathlib import Path as _P
    assert indexer_jar(prof) == _P.cwd() / "vendor" / "x.jar"
