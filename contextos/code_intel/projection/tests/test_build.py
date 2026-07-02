"""build_projection: 成功路径换新+meta 齐全 / runner 炸保旧 / 非零检查失败保旧 /
unresolved 超标换新但 degraded / 抽样对照超阈整体回滚真保旧。runner/loader/sampler 全注入。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection import store
from contextos.code_intel.projection.build import build_projection
from contextos.code_intel.projection.indexer_runner import IndexerError


def _rows(resolved: int = 1) -> dict:
    return {
        "code_files": [{"file_path": "src/A.java", "sha1": "a" * 40}],
        "code_classes": [{"class_id": "c1", "class_fqn": "com.acme.A", "class_name": "A",
                          "name_lower": "a", "source_file": "src/A.java"}],
        "code_methods": [{"method_id": "m1", "class_fqn": "com.acme.A",
                          "method_name": "run", "name_lower": "run",
                          "method_fqn": "com.acme.A.run()", "source_file": "src/A.java"}],
        "code_fields": [{"field_id": "f1", "class_fqn": "com.acme.A",
                         "field_name": "K", "name_lower": "k", "source_file": "src/A.java"}],
        "code_calls": [{"call_id": "k1", "caller_method_fqn": "com.acme.A.run()",
                        "resolved": resolved, "source_file": "src/A.java"}],
        "code_references": [{"source_fqn": "com.acme.A", "source_file": "src/A.java",
                             "target_fqn": "com.acme.B", "target_kind": "type",
                             "ref_kind": "use", "line_no": 1, "column_no": 1}],
        "code_inheritance": [{"sub_class_fqn": "com.acme.A", "super_class_fqn": "com.acme.S",
                              "relation_type": "extends", "source_file": "src/A.java"}],
        "code_table_refs": [],
    }


def _kw(tmp_path: Path, engine, **over) -> dict[str, Any]:
    # 显式 dict[str, Any]: 不标注时 pyright 推成异构 union, `**_kw(...)` 解包对每个
    # 具名参数都报不可赋值(每调用点 ~13 条假阳, 全文件曾 156+ 条)。
    kw: dict[str, Any] = dict(
        engine=engine, repo_root=tmp_path, java_home="", jar=tmp_path / "x.jar",
        xmx="1g", build_ctx={"java_version": "1.8", "modules": []},
        out_dir=tmp_path / "out", indexed_commit="abc123",
        runner=lambda **_: None, loader=lambda *_a, **_k: _rows(),
        sampler=None, unresolved_max=0.15, sample_max_mismatch=0.05)
    kw.update(over)
    return kw


@pytest.fixture
def ready_engine(engine, tmp_path):
    S.ensure_projection_schema(engine)
    (tmp_path / "x.jar").write_bytes(b"PK")
    return engine


def test_success_swaps_and_stamps_meta(ready_engine, tmp_path):
    res = build_projection(**_kw(tmp_path, ready_engine))
    assert res["status"] == "ok"
    assert store.table_counts(ready_engine)["code_classes"] == 1
    assert store.get_meta(ready_engine, "last_indexed_commit") == "abc123"
    assert store.get_meta(ready_engine, "projection_build_id")
    assert store.get_meta(ready_engine, "build_status") == "ok"
    assert store.get_meta(ready_engine, "build_context_hash")
    assert store.get_meta(ready_engine, "jar_hash")
    assert store.get_meta(ready_engine, "jdk_fingerprint")
    assert res["counts"]["code_classes"] == 1


def test_runner_failure_keeps_old(ready_engine, tmp_path):
    build_projection(**_kw(tmp_path, ready_engine))  # 先有旧投影

    def boom(**_):
        raise IndexerError("jar exploded")

    res = build_projection(**_kw(tmp_path, ready_engine, runner=boom, indexed_commit="new1"))
    assert res["status"] == "degraded"
    assert "jar exploded" in res["detail"]
    assert store.get_meta(ready_engine, "last_indexed_commit") == "abc123"  # 旧 meta 没动
    assert store.table_counts(ready_engine)["code_classes"] == 1            # 旧数据没动


def test_nonzero_check_failure_keeps_old(ready_engine, tmp_path):
    build_projection(**_kw(tmp_path, ready_engine))
    empty = _rows()
    empty["code_methods"] = []   # 6 表非零检查应失败
    res = build_projection(**_kw(tmp_path, ready_engine,
                                 loader=lambda *_a, **_k: empty, indexed_commit="new1"))
    assert res["status"] == "degraded"
    assert "nonzero" in res["detail"]
    assert store.get_meta(ready_engine, "last_indexed_commit") == "abc123"
    assert store.table_counts(ready_engine)["code_classes"] == 1


def test_soft_tables_empty_swaps_but_degraded(ready_engine, tmp_path):
    """F2: 无继承/调用/字段/引用的合法小仓不能被硬闸 brick —— 软表全空时
    换新(counts 非零)但 status=degraded + detail 列出空表名。"""
    soft_empty = _rows()
    for name in ("code_fields", "code_calls", "code_references", "code_inheritance"):
        soft_empty[name] = []
    res = build_projection(**_kw(tmp_path, ready_engine,
                                 loader=lambda *_a, **_k: soft_empty))
    assert res["status"] == "degraded"
    assert "empty" in res["detail"]
    assert "code_inheritance" in res["detail"]
    assert store.table_counts(ready_engine)["code_classes"] == 1   # 换新了
    assert store.get_meta(ready_engine, "build_status") == "degraded"


def test_unresolved_over_threshold_swaps_but_degraded(ready_engine, tmp_path):
    res = build_projection(**_kw(tmp_path, ready_engine,
                                 loader=lambda *_a, **_k: _rows(resolved=0)))
    assert res["status"] == "degraded"
    assert "unresolved" in res["detail"]
    assert store.table_counts(ready_engine)["code_classes"] == 1   # 换新了
    assert store.get_meta(ready_engine, "build_status") == "degraded"


def test_sampler_mismatch_keeps_old(ready_engine, tmp_path):
    """第三轮 review HIGH: 抽样超阈必须**真保旧**(事务回滚), 不是只挡 meta。"""
    build_projection(**_kw(tmp_path, ready_engine))
    new_rows = _rows()
    new_rows["code_classes"] = [{**new_rows["code_classes"][0],
                                 "class_id": "c2", "class_fqn": "com.acme.New",
                                 "class_name": "New", "name_lower": "new"}]
    res = build_projection(**_kw(tmp_path, ready_engine, indexed_commit="new1",
                                 loader=lambda *_a, **_k: new_rows,
                                 sampler=lambda conn: 0.5))   # 50% 偏差 > 5%
    assert res["status"] == "degraded"
    assert "sample" in res["detail"]
    with ready_engine.connect() as conn:
        fqns = [r[0] for r in conn.execute(select(S.code_classes.c.class_fqn))]
    assert fqns == ["com.acme.A"]    # 旧行还在, 新行(com.acme.New)被回滚
    assert store.get_meta(ready_engine, "last_indexed_commit") == "abc123"
    assert store.get_meta(ready_engine, "build_status") == "ok"   # 旧 meta 原样


def test_rows_and_meta_atomic_on_late_failure(ready_engine, tmp_path):
    """F3+F6 kill-test: sampler 读过 staging 行后猝死(JDT 死等, 非对照超阈)->
    degraded + **行和 meta 都未变**。双事务变体(行 commit 后再写 meta)下行已
    换新, 本断言必红 —— 锁死 行+meta 单事务原子契约。"""
    build_projection(**_kw(tmp_path, ready_engine))   # 先有旧投影
    new_rows = _rows()
    new_rows["code_classes"] = [{**new_rows["code_classes"][0],
                                 "class_id": "c2", "class_fqn": "com.acme.New",
                                 "class_name": "New", "name_lower": "new"}]

    def crash_sampler(conn):
        conn.execute(select(S.code_classes.c.class_fqn)).fetchall()  # 已读 staging 行
        raise RuntimeError("jdt died mid-sample")

    res = build_projection(**_kw(tmp_path, ready_engine, indexed_commit="new1",
                                 loader=lambda *_a, **_k: new_rows,
                                 sampler=crash_sampler))
    assert res["status"] == "degraded"
    assert "sampler crashed" in res["detail"]
    assert "RuntimeError" in res["detail"]
    with ready_engine.connect() as conn:
        fqns = [r[0] for r in conn.execute(select(S.code_classes.c.class_fqn))]
    assert fqns == ["com.acme.A"]    # 行回滚: 新行(com.acme.New)没进库
    assert store.get_meta(ready_engine, "last_indexed_commit") == "abc123"  # meta 原样
    assert store.get_meta(ready_engine, "build_status") == "ok"


def test_sampler_reads_staging_rows(ready_engine, tmp_path):
    """sampler 拿到的 Connection 必须看得到本事务未 commit 的新行(staging 语义)。"""
    seen: list[list[str]] = []

    def probe_sampler(conn):
        fqns = [r[0] for r in conn.execute(select(S.code_classes.c.class_fqn))]
        seen.append(fqns)
        return 0.0

    build_projection(**_kw(tmp_path, ready_engine, sampler=probe_sampler))
    assert seen == [["com.acme.A"]]   # 事务内可见新行


def test_staging_swap_unexpected_error_degraded_keeps_old(ready_engine, tmp_path):
    """HIGH-2(最终 review)兜底契约: staging 事务内任何非预期异常(此处注入未知表名
    -> insert_rows_conn KeyError; v3 代理 PK 后重复 inheritance 行已不撞约束)不裸抛,
    兜成 degraded + 保旧(engine.begin 上下文 raise 即回滚)。
    与 runner/loader 失败返 degraded 对称; _SampleMismatch/_SamplerCrash 专属
    catch 仍优先(放兜底之前)。"""
    build_projection(**_kw(tmp_path, ready_engine))   # 先有旧投影
    dup = _rows()
    dup["code_bogus_table"] = [{"x": 1}]              # 未知表 -> staging 事务内 KeyError
    res = build_projection(**_kw(tmp_path, ready_engine, indexed_commit="new1",
                                 loader=lambda *_a, **_k: dup))
    assert res["status"] == "degraded"
    assert "swap failed" in res["detail"]
    assert store.get_meta(ready_engine, "last_indexed_commit") == "abc123"   # 保旧
    assert store.table_counts(ready_engine)["code_inheritance"] == 1
    assert store.get_meta(ready_engine, "build_status") == "ok"              # 旧 meta 原样
