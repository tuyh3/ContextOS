"""build_context: source_roots 解析(相对仓根/绝对) / 仓内 jar 收集 + 排除 build 产物 /
extra_classpath_dirs / 指纹 hash 稳定。"""
from __future__ import annotations

import json
from pathlib import Path

from contextos.code_intel.projection.build_context import (
    build_context_dict,
    context_fingerprint,
)


def _mk_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src/main/java").mkdir(parents=True)
    (repo / "lib").mkdir()
    (repo / "lib/dep.jar").write_bytes(b"PK")
    (repo / "build/libs").mkdir(parents=True)
    (repo / "build/libs/out.jar").write_bytes(b"PK")   # 应被排除(/build/)
    return repo


def test_basic_shape(tmp_path, make_profile):
    repo = _mk_repo(tmp_path)
    prof = make_profile(project_path=str(repo), source_roots=["src/main/java"])
    ctx = build_context_dict(prof)
    assert ctx["java_version"] == "1.8"
    assert len(ctx["modules"]) == 1
    m = ctx["modules"][0]
    assert m["source_roots"] == [str((repo / "src/main/java").resolve())]
    assert str((repo / "lib/dep.jar").resolve()) in m["classpath_entries"]
    assert all("/build/" not in e for e in m["classpath_entries"])
    assert m["encoding"] == "UTF-8"


def test_extra_classpath_dirs(tmp_path, make_profile):
    repo = _mk_repo(tmp_path)
    extra = tmp_path / "ext"
    extra.mkdir()
    (extra / "x.jar").write_bytes(b"PK")
    prof = make_profile(project_path=str(repo), source_roots=["src/main/java"],
                        extra_classpath_dirs=[str(extra)])
    ctx = build_context_dict(prof)
    assert str((extra / "x.jar").resolve()) in ctx["modules"][0]["classpath_entries"]


def test_jar_filename_dedup(tmp_path, make_profile):
    repo = _mk_repo(tmp_path)
    (repo / "lib2").mkdir()
    (repo / "lib2/dep.jar").write_bytes(b"PKPK")   # 同名不同路径 -> 去重保首个
    prof = make_profile(project_path=str(repo), source_roots=["src/main/java"])
    ctx = build_context_dict(prof)
    names = [Path(e).name for e in ctx["modules"][0]["classpath_entries"]]
    assert names.count("dep.jar") == 1


def test_fingerprint_stable_and_sensitive(tmp_path, make_profile):
    repo = _mk_repo(tmp_path)
    prof = make_profile(project_path=str(repo), source_roots=["src/main/java"])
    ctx = build_context_dict(prof)
    fp1 = context_fingerprint(ctx)
    fp2 = context_fingerprint(json.loads(json.dumps(ctx)))   # round-trip 稳定
    assert fp1 == fp2
    ctx["modules"][0]["classpath_entries"].append("/other.jar")
    assert context_fingerprint(ctx) != fp1
