"""增量: Layer2 扫描产 added/modified/deleted(added 含未跟踪新文件, code_files
只是基准不是范围) / 扩展变更集经 code_references(含 deleted) / 回退条件 /
子集重建删旧插新 / 越界锚防御。git 层经注入的 git_changed_files 函数。"""
from __future__ import annotations

import hashlib
import shutil as _shutil
from pathlib import Path

import pytest

from sqlalchemy import select

from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection import store
from contextos.code_intel.projection.incremental import (
    ChangeSet, detect_changes, expand_changed, fingerprints_changed,
    git_changed_files_real, plan_incremental, run_incremental,
)


def _sha1(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def _seed(engine, tmp_path: Path) -> Path:
    """两个已索引文件 A/B + code_files 基准; B 引用 A(扩展变更集用)。"""
    S.ensure_projection_schema(engine)
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "A.java").write_text("class A {}")
    (src / "B.java").write_text("class B { A a; }")
    store.replace_all(engine, {
        "code_files": [
            {"file_path": "src/A.java", "sha1": _sha1(src / "A.java")},
            {"file_path": "src/B.java", "sha1": _sha1(src / "B.java")}],
        "code_classes": [
            {"class_id": "a", "class_fqn": "A", "class_name": "A", "name_lower": "a",
             "source_file": "src/A.java"},
            {"class_id": "b", "class_fqn": "B", "class_name": "B", "name_lower": "b",
             "source_file": "src/B.java"}],
        "code_references": [
            {"source_fqn": "B", "source_file": "src/B.java", "target_fqn": "A",
             "target_kind": "type", "ref_kind": "field_type", "line_no": 1, "column_no": 1}],
    })
    store.set_meta(engine, "last_indexed_commit", "base")
    store.set_meta(engine, "projection_build_id", "seed1")   # 基准信号(非 git 仓修订)
    return repo


def test_layer2_added_modified_deleted(engine, tmp_path):
    repo = _seed(engine, tmp_path)
    (repo / "src/C.java").write_text("class C {}")          # added(未跟踪, 不在 code_files)
    (repo / "src/A.java").write_text("class A { int x; }")  # modified(sha1 变)
    (repo / "src/B.java").unlink()                          # deleted
    cs: ChangeSet = detect_changes(
        engine, repo_root=repo, source_roots=[repo / "src"],
        exclude_dirs=[], git_changed_files=lambda *_: [])
    assert cs.added == ["src/C.java"]
    assert cs.modified == ["src/A.java"]
    assert cs.deleted == ["src/B.java"]


def test_layer2_exclude_dirs(engine, tmp_path):
    repo = _seed(engine, tmp_path)
    gen = repo / "src" / "generated"
    gen.mkdir()
    (gen / "G.java").write_text("class G {}")
    cs = detect_changes(engine, repo_root=repo, source_roots=[repo / "src"],
                        exclude_dirs=["generated"], git_changed_files=lambda *_: [])
    assert cs.added == []                                   # 被 exclude


def test_git_layer_merged(engine, tmp_path):
    repo = _seed(engine, tmp_path)
    cs = detect_changes(engine, repo_root=repo, source_roots=[repo / "src"],
                        exclude_dirs=[],
                        git_changed_files=lambda *_: ["src/Gone.java"])
    # git 报了但文件系统不存在且不在基准 -> deleted 类(容错路径)
    assert "src/Gone.java" in cs.deleted


def test_git_out_of_scope_filtered(engine, tmp_path):
    """F4: git 报 source_roots 之外 / exclude 目录下的 .java 不得进任何桶 ——
    否则全落 deleted 污染计数 + 虚增阈值(codegen 大 commit 一次伪 full 信号)。"""
    repo = _seed(engine, tmp_path)
    cs = detect_changes(
        engine, repo_root=repo, source_roots=[repo / "src"], exclude_dirs=["gen"],
        git_changed_files=lambda *_: ["other/Y.java", "src/gen/G.java", "src/X.java"])
    assert "src/X.java" in cs.deleted     # in-scope, FS 无 + 不在基准 -> deleted 容错桶
    for rel in ("other/Y.java", "src/gen/G.java"):
        assert rel not in cs.added + cs.modified + cs.deleted


def test_expand_changed_pulls_referencers(engine, tmp_path):
    _seed(engine, tmp_path)
    expanded = expand_changed(engine, ["src/A.java"])
    assert "src/B.java" in expanded     # B 引用了 A 文件里定义的 FQN


def test_deleted_file_pulls_referencer_reparse(engine, tmp_path):
    """review HIGH: 删 A.java -> B(引用方)必须被拉入重解析, 否则 B 的旧 resolved
    reference 残留成脏索引。"""
    repo = _seed(engine, tmp_path)
    (repo / "src/A.java").unlink()
    captured: dict = {}

    def fake_runner(**kw):
        captured["files"] = kw["files_list"].read_text()

    def fake_loader(out_dir, *, repo_root):
        return {"code_files": [{"file_path": "src/B.java", "sha1": "m" * 40}],
                "code_classes": [{"class_id": "b2", "class_fqn": "B", "class_name": "B",
                                  "name_lower": "b", "source_file": "src/B.java"}],
                "code_references": []}   # B 重解析后对 A 的引用消失(诚实反映删除)

    res = run_incremental(
        engine=engine, repo_root=repo, source_roots=[repo / "src"], exclude_dirs=[],
        java_home="", jar=tmp_path / "x.jar", xmx="1g",
        build_ctx={"java_version": "1.8", "modules": []}, out_dir=tmp_path / "out",
        head_commit="head2", git_changed_files=lambda *_: [],
        runner=fake_runner, loader=fake_loader, max_files=500)
    assert res["status"] == "ok"
    assert str(repo / "src/B.java") in captured["files"]      # 引用方被重解析
    assert str(repo / "src/A.java") not in captured["files"]  # 已删除文件不喂 jar
    with engine.connect() as conn:
        fqns = [r[0] for r in conn.execute(select(S.code_classes.c.class_fqn))]
        refs = conn.execute(select(S.code_references.c.ref_id)).fetchall()
    assert fqns == ["B"]                          # A 的行全删
    assert refs == []                             # stale reference 清掉


def test_plan_full_rebuild_when_over_threshold(engine, tmp_path):
    _seed(engine, tmp_path)
    files = [f"src/F{i}.java" for i in range(600)]
    assert plan_incremental(engine, changed=files, max_files=500) == "full"


def test_plan_full_rebuild_when_no_baseline(engine):
    S.ensure_projection_schema(engine)   # 无 last_indexed_commit
    assert plan_incremental(engine, changed=["src/A.java"], max_files=500) == "full"


def test_plan_noop(engine, tmp_path):
    _seed(engine, tmp_path)
    assert plan_incremental(engine, changed=[], max_files=500) == "noop"


def test_run_incremental_subset_swap(engine, tmp_path):
    repo = _seed(engine, tmp_path)
    (repo / "src/A.java").write_text("class A2 {}")

    def fake_runner(**kw):   # 真 jar 不跑; 断言传了 --files 清单
        assert kw.get("files_list") is not None

    def fake_loader(out_dir, *, repo_root):
        return {"code_files": [{"file_path": "src/A.java", "sha1": "n" * 40}],
                "code_classes": [{"class_id": "a2", "class_fqn": "A2", "class_name": "A2",
                                  "name_lower": "a2", "source_file": "src/A.java"}]}

    res = run_incremental(
        engine=engine, repo_root=repo, source_roots=[repo / "src"], exclude_dirs=[],
        java_home="", jar=tmp_path / "x.jar", xmx="1g",
        build_ctx={"java_version": "1.8", "modules": []}, out_dir=tmp_path / "out",
        head_commit="head1", git_changed_files=lambda *_: [],
        runner=fake_runner, loader=fake_loader, max_files=500)
    assert res["status"] == "ok"
    with engine.connect() as conn:
        fqns = sorted(r[0] for r in conn.execute(select(S.code_classes.c.class_fqn)))
    # A 的旧行(A)换成新行(A2); B 被扩展重解析但 fake_loader 没给 B 行 ->
    # B 旧行删除(扩展集里的文件同样删旧插新)
    assert fqns == ["A2"]
    assert store.get_meta(engine, "last_indexed_commit") == "head1"


def test_out_of_scope_anchor_filtered(engine, tmp_path):
    """第三轮 review MEDIUM: 子集产出越界锚(不在 reparse 集合)丢弃, 空锚保留。"""
    repo = _seed(engine, tmp_path)
    (repo / "src/A.java").write_text("class A2 {}")

    def fake_loader(out_dir, *, repo_root):
        return {"code_classes": [
            {"class_id": "a2", "class_fqn": "A2", "class_name": "A2",
             "name_lower": "a2", "source_file": "src/A.java"},          # 在 reparse 内
            {"class_id": "zz", "class_fqn": "ZZ", "class_name": "ZZ",
             "name_lower": "zz", "source_file": "src/Other.java"},      # 越界 -> 丢
            {"class_id": "e0", "class_fqn": "E0", "class_name": "E0",
             "name_lower": "e0", "source_file": ""}]}                   # 空锚 -> 留

    run_incremental(
        engine=engine, repo_root=repo, source_roots=[repo / "src"], exclude_dirs=[],
        java_home="", jar=tmp_path / "x.jar", xmx="1g",
        build_ctx={"java_version": "1.8", "modules": []}, out_dir=tmp_path / "out",
        head_commit="h", git_changed_files=lambda *_: [],
        runner=lambda **_: None, loader=fake_loader, max_files=500)
    with engine.connect() as conn:
        fqns = sorted(r[0] for r in conn.execute(select(S.code_classes.c.class_fqn)))
    assert "ZZ" not in fqns
    assert "E0" in fqns and "A2" in fqns


def _run_kw(engine, repo: Path, tmp_path: Path, **over) -> dict:
    kw = dict(
        engine=engine, repo_root=repo, source_roots=[repo / "src"], exclude_dirs=[],
        java_home="", jar=tmp_path / "x.jar", xmx="1g",
        build_ctx={"java_version": "1.8", "modules": []}, out_dir=tmp_path / "out",
        head_commit="h", git_changed_files=lambda *_: [],
        runner=lambda **_: None, max_files=500)
    kw.update(over)
    return kw


def test_incremental_unresolved_over_threshold_degrades_build_status(engine, tmp_path):
    """F5: 子集在坏环境 100% unresolved -> 仍换新(status ok)但 build_status
    meta 降 degraded + detail 警示, freshness 不再撒谎。"""
    repo = _seed(engine, tmp_path)
    store.set_meta(engine, "build_status", "ok")   # 全量曾经 ok
    (repo / "src/A.java").write_text("class A2 {}")

    def bad_loader(out_dir, *, repo_root):
        return {"code_files": [{"file_path": "src/A.java", "sha1": "n" * 40}],
                "code_classes": [{"class_id": "a2", "class_fqn": "A2", "class_name": "A2",
                                  "name_lower": "a2", "source_file": "src/A.java"}],
                "code_calls": [{"call_id": "k1", "caller_method_fqn": "A2.run()",
                                "resolved": 0, "source_file": "src/A.java"}]}

    res = run_incremental(**_run_kw(engine, repo, tmp_path, loader=bad_loader))
    assert res["status"] == "ok"                    # 换新成功
    assert "unresolved" in res["detail"]
    assert store.get_meta(engine, "build_status") == "degraded"
    with engine.connect() as conn:
        fqns = sorted(r[0] for r in conn.execute(select(S.code_classes.c.class_fqn)))
    assert "A2" in fqns                             # 数据确实换新了


def test_incremental_normal_subset_keeps_build_status(engine, tmp_path):
    """F5 反向: 子集质量正常 -> 不碰 build_status(保全仓语义)。"""
    repo = _seed(engine, tmp_path)
    store.set_meta(engine, "build_status", "ok")
    (repo / "src/A.java").write_text("class A2 {}")

    def good_loader(out_dir, *, repo_root):
        return {"code_files": [{"file_path": "src/A.java", "sha1": "n" * 40}],
                "code_classes": [{"class_id": "a2", "class_fqn": "A2", "class_name": "A2",
                                  "name_lower": "a2", "source_file": "src/A.java"}],
                "code_calls": [{"call_id": "k1", "caller_method_fqn": "A2.run()",
                                "resolved": 1, "source_file": "src/A.java"}]}

    res = run_incremental(**_run_kw(engine, repo, tmp_path, loader=good_loader))
    assert res["status"] == "ok"
    assert "unresolved" not in res["detail"]
    assert store.get_meta(engine, "build_status") == "ok"


def test_git_failure_signals_full_rebuild(engine, tmp_path):
    repo = _seed(engine, tmp_path)

    def broken_git(*_):
        raise RuntimeError("merge-base broken")

    res = run_incremental(
        engine=engine, repo_root=repo, source_roots=[repo / "src"], exclude_dirs=[],
        java_home="", jar=tmp_path / "x.jar", xmx="1g",
        build_ctx={"java_version": "1.8", "modules": []}, out_dir=tmp_path / "out",
        head_commit="h", git_changed_files=broken_git,
        runner=lambda **_: None, loader=lambda *_a, **_k: {}, max_files=500)
    assert res["status"] == "full_rebuild_required"


def test_git_binary_missing_signals_full_rebuild(engine, tmp_path):
    """NIT8: git binary 不存在(FileNotFoundError)也走全量回退信号, 不裸抛。"""
    repo = _seed(engine, tmp_path)

    def no_git(*_):
        raise FileNotFoundError("git binary not found")

    res = run_incremental(**_run_kw(engine, repo, tmp_path,
                                    git_changed_files=no_git,
                                    loader=lambda *_a, **_k: {}))
    assert res["status"] == "full_rebuild_required"
    assert "git" in res["detail"]


# --- HIGH-1(最终 review): 指纹只写不比 -> 增量入口(spec §3.1 条件 2)强制比对 ---
# 设计思路: build_projection 把 build_context_hash/jar_hash/jdk_fingerprint 入档
# code_projection_meta, 但此前全库无人比对 —— 换 jar / 改 build_ctx 后增量照跑,
# 产出"半新半旧"投影且 meta 撒谎(增量不更新指纹)。修法 = run_incremental 在
# detect_changes 之前调 fingerprints_changed, 不一致返 full_rebuild_required
# (rebuild_entry 同锁内自动接全量, watcher/MCP/CLI 全部受益)。
# 评分: 指纹变 -> full_rebuild_required + detail 含 fingerprint 与变更键名;
#       一致 / 无基准 -> 正常路径不受影响。


def _stamp_fingerprints(engine, *, jar: Path, build_ctx: dict, java_home: str) -> None:
    """模拟上次全量 build 入档的运行时指纹(与 build_projection 同口径)。"""
    import platform

    from contextos.code_intel.projection.build_context import context_fingerprint
    from contextos.code_intel.projection.indexer_runner import jar_fingerprint
    store.set_meta(engine, "jar_hash", jar_fingerprint(jar) if jar.exists() else "")
    store.set_meta(engine, "build_context_hash", context_fingerprint(build_ctx))
    store.set_meta(engine, "jdk_fingerprint", f"{java_home}|{platform.machine()}")


def test_fingerprints_changed_none_when_no_baseline_or_consistent(engine, tmp_path):
    """无基准(首建, meta 三键全缺)返 None(首建回退由 plan_incremental 兜);
    入档与当前一致也返 None。"""
    S.ensure_projection_schema(engine)
    jar = tmp_path / "x.jar"
    jar.write_bytes(b"PK-v1")
    ctx = {"java_version": "1.8", "modules": []}
    assert fingerprints_changed(engine, jar=jar, build_ctx=ctx, java_home="/jdk8") is None
    _stamp_fingerprints(engine, jar=jar, build_ctx=ctx, java_home="/jdk8")
    assert fingerprints_changed(engine, jar=jar, build_ctx=ctx, java_home="/jdk8") is None


def test_fingerprints_changed_reports_each_diff_key(engine, tmp_path):
    """换 jar 字节 / 改 build_ctx / 换 java_home -> 对应键名进返回串(调用方可诊断)。"""
    S.ensure_projection_schema(engine)
    jar = tmp_path / "x.jar"
    jar.write_bytes(b"PK-v1")
    ctx = {"java_version": "1.8", "modules": []}
    _stamp_fingerprints(engine, jar=jar, build_ctx=ctx, java_home="/jdk8")
    jar.write_bytes(b"PK-v2-swapped")
    diff = fingerprints_changed(engine, jar=jar, build_ctx=ctx, java_home="/jdk8")
    assert diff is not None and "jar_hash" in diff
    jar.write_bytes(b"PK-v1")                                    # jar 还原
    diff = fingerprints_changed(engine, jar=jar,
                                build_ctx={"java_version": "11", "modules": []},
                                java_home="/jdk8")
    assert diff is not None and "build_context_hash" in diff
    diff = fingerprints_changed(engine, jar=jar, build_ctx=ctx, java_home="/jdk21")
    assert diff is not None and "jdk_fingerprint" in diff


def test_run_incremental_fingerprint_mismatch_forces_full(engine, tmp_path):
    """HIGH-1 主断言: 换 jar 后 run_incremental 必须返 full_rebuild_required
    (不准照跑增量产半新半旧投影), detail 含 fingerprint 可诊断。"""
    repo = _seed(engine, tmp_path)
    jar = tmp_path / "x.jar"
    jar.write_bytes(b"PK-v1")
    ctx = {"java_version": "1.8", "modules": []}
    _stamp_fingerprints(engine, jar=jar, build_ctx=ctx, java_home="")
    jar.write_bytes(b"PK-v2-swapped")              # 换 jar 字节
    (repo / "src/A.java").write_text("class A2 {}")

    def _boom_runner(**_):
        raise AssertionError("incremental must not run jar on fingerprint mismatch")

    res = run_incremental(**_run_kw(engine, repo, tmp_path, jar=jar, build_ctx=ctx,
                                    runner=_boom_runner, loader=lambda *_a, **_k: {}))
    assert res["status"] == "full_rebuild_required"
    assert "fingerprint" in res["detail"] and "jar_hash" in res["detail"]


def test_run_incremental_fingerprint_match_runs_incremental(engine, tmp_path):
    """指纹一致 -> 正常增量(回归保护: 闸门不误伤)。"""
    repo = _seed(engine, tmp_path)
    jar = tmp_path / "x.jar"
    jar.write_bytes(b"PK-v1")
    ctx = {"java_version": "1.8", "modules": []}
    _stamp_fingerprints(engine, jar=jar, build_ctx=ctx, java_home="")
    (repo / "src/A.java").write_text("class A2 {}")

    def good_loader(out_dir, *, repo_root):
        return {"code_files": [{"file_path": "src/A.java", "sha1": "n" * 40}],
                "code_classes": [{"class_id": "a2", "class_fqn": "A2", "class_name": "A2",
                                  "name_lower": "a2", "source_file": "src/A.java"}]}

    res = run_incremental(**_run_kw(engine, repo, tmp_path, jar=jar, build_ctx=ctx,
                                    loader=good_loader))
    assert res["status"] == "ok"
    assert res["reparsed"] >= 1


def test_non_git_repo_incremental_works(engine, tmp_path):
    """真某电信客户项目实锤回归: 工作目录不是 git 仓 -> last_indexed_commit 恒空, 但只要
    build 发生过(projection_build_id 在), 增量走 Layer-2-only, 不许误判全量。"""
    repo = _seed(engine, tmp_path)
    store.set_meta(engine, "last_indexed_commit", "")        # 非 git 仓形态
    (repo / "src/A.java").write_text("class A2 {}")

    def fake_loader(out_dir, *, repo_root):
        return {"code_files": [{"file_path": "src/A.java", "sha1": "n" * 40}],
                "code_classes": [{"class_id": "a2", "class_fqn": "A2", "class_name": "A2",
                                  "name_lower": "a2", "source_file": "src/A.java"}]}

    res = run_incremental(
        engine=engine, repo_root=repo, source_roots=[repo / "src"], exclude_dirs=[],
        java_home="", jar=tmp_path / "x.jar", xmx="1g",
        build_ctx={"java_version": "1.8", "modules": []}, out_dir=tmp_path / "out",
        head_commit="", git_changed_files=git_changed_files_real,   # 真 Layer 1: 空 since -> []
        runner=lambda **_: None, loader=fake_loader, max_files=500)
    assert res["status"] == "ok"          # 不是 full_rebuild_required
    assert res["modified"] == 1


def test_git_layer_empty_since_returns_empty():
    """git_changed_files_real 对空 since_commit(非 git 仓 / pre-git 基线)返回空清单。"""
    from pathlib import Path as _P
    assert git_changed_files_real(_P("/nonexistent"), "") == []


def test_out_of_repo_source_root_does_not_crash(engine, tmp_path):
    """merge-review HIGH 回归: profile 允许仓外绝对 source root(paths.resolve_source_roots
    实测放行), 旧扫描对仓外文件强行 relative_to(repo_root) -> ValueError 崩增量。
    修后: 仓外文件用绝对路径作锚(与 jsonl_load._rel 同口径), added/modified/deleted 正常。"""
    repo = _seed(engine, tmp_path)
    ext = tmp_path / "external-src"
    ext.mkdir()
    (ext / "X.java").write_text("class X {}")
    cs = detect_changes(engine, repo_root=repo, source_roots=[repo / "src", ext],
                        exclude_dirs=[], git_changed_files=lambda *_: [])
    assert (ext / "X.java").as_posix() in cs.added     # 仓外文件 = as_posix 绝对路径锚
    # 仓外 root 内 exclude 仍生效(按 root 相对段)
    gen = ext / "generated"
    gen.mkdir()
    (gen / "G.java").write_text("class G {}")
    cs2 = detect_changes(engine, repo_root=repo, source_roots=[repo / "src", ext],
                         exclude_dirs=["generated"], git_changed_files=lambda *_: [])
    assert all("G.java" not in f for f in cs2.added)


def test_duplicate_sub_super_across_files_coexist(engine, tmp_path):
    """merge-review MEDIUM 回归: 重复 FQN 世界里同 (sub,super) 锚在两个文件,
    增量只重解析其一 —— v3 代理 PK 后插入不再撞复合 PK(旧 schema IntegrityError 冒泡)。"""
    repo = _seed(engine, tmp_path)
    from sqlalchemy import insert as _ins
    with engine.begin() as conn:
        conn.execute(_ins(S.code_inheritance), [{
            "sub_class_fqn": "Dup", "super_class_fqn": "Base",
            "relation_type": "extends", "source_file": "src/Other.java"}])
    (repo / "src/A.java").write_text("class A2 {}")

    def loader(out_dir, *, repo_root):
        return {"code_files": [{"file_path": "src/A.java", "sha1": "z" * 40}],
                "code_inheritance": [{
                    "sub_class_fqn": "Dup", "super_class_fqn": "Base",
                    "relation_type": "extends", "source_file": "src/A.java"}]}

    res = run_incremental(
        engine=engine, repo_root=repo, source_roots=[repo / "src"], exclude_dirs=[],
        java_home="", jar=tmp_path / "x.jar", xmx="1g",
        build_ctx={"java_version": "1.8", "modules": []}, out_dir=tmp_path / "out",
        head_commit="", git_changed_files=lambda *_: [],
        runner=lambda **_: None, loader=loader, max_files=500)
    assert res["status"] == "ok"                      # 不再 IntegrityError 冒泡
    with engine.connect() as conn:
        n = conn.execute(select(S.code_inheritance.c.row_id).where(
            (S.code_inheritance.c.sub_class_fqn == "Dup"))).fetchall()
    assert len(n) == 2                                # 两文件的行共存


@pytest.mark.cmd_boundary
def test_git_changed_files_real_z_non_ascii(tmp_path):
    """站点5: git diff --name-only -z 出原始字节(无 quotepath 八进制), 非 ASCII / 空格 .java
    经 os.fsdecode 正确还原; 非 .java 被过滤。真 git 仓 smoke(三平台 CI)。"""
    if _shutil.which("git") is None:
        import pytest
        pytest.skip("git not installed")
    import subprocess as _sp
    repo = tmp_path / "g"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "中文 Name.java").write_text("class A{}\n", encoding="utf-8")
    (repo / "src" / "note.txt").write_text("x\n", encoding="utf-8")
    env = {**__import__("os").environ}
    _sp.run(["git", "-C", str(repo), "init", "-q"], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.email", "t@t.t"], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    _sp.run(["git", "-C", str(repo), "add", "src/中文 Name.java", "src/note.txt"], check=True)
    _sp.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True, env=env)
    (repo / "src" / "中文 Name.java").write_text("class A{int x;}\n", encoding="utf-8")
    (repo / "src" / "note.txt").write_text("y\n", encoding="utf-8")
    _sp.run(["git", "-C", str(repo), "add", "src/中文 Name.java", "src/note.txt"], check=True)
    _sp.run(["git", "-C", str(repo), "commit", "-qm", "c2"], check=True, env=env)
    base = _sp.run(["git", "-C", str(repo), "rev-parse", "HEAD~1"],
                   capture_output=True, text=True).stdout.strip()
    changed = git_changed_files_real(repo, base)
    assert "src/中文 Name.java" in changed     # 非 ASCII + 空格还原正确
    assert all(c.endswith(".java") for c in changed)   # note.txt 被过滤


@pytest.mark.cmd_boundary
def test_head_commit_real_returns_sha(tmp_path):
    """站点6: head_commit_real 取 HEAD sha(ASCII, decode_content)。"""
    from contextos.code_intel.projection.incremental import head_commit_real   # 顶部 import 块未含, 本地引入
    if _shutil.which("git") is None:
        import pytest
        pytest.skip("git not installed")
    import subprocess as _sp
    repo = tmp_path / "h"
    repo.mkdir()
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    _sp.run(["git", "-C", str(repo), "init", "-q"], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.email", "t@t.t"], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    _sp.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    _sp.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True,
            env={**__import__("os").environ})
    sha = head_commit_real(repo)
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


def test_in_scope_respects_directory_boundary(tmp_path):
    """LOW 回归(Windows 阶段2 附录B): root=repo/src 不应误配 repo/src-extra/X.java。
    is_relative_to 是 OS 感知的边界判定, 等价旧 startswith(str(root)+"/") 在
    POSIX 上的效果, 但不依赖硬编码 "/" 分隔符(Windows 上才有实差, 见 spec §2)。"""
    from contextos.code_intel.projection.incremental import _in_scope
    repo = tmp_path / "repo"
    root = repo / "src"
    assert _in_scope("src/A.java", repo, [root], []) is True
    assert _in_scope("src-extra/X.java", repo, [root], []) is False
    assert _in_scope("src/sub/B.java", repo, [root], []) is True
