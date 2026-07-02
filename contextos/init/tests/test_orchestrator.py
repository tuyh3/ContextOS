"""Task 6 (Block 2): run_init 顶层编排测试。

Design intent:
  - run_init 是 contextos init 的核心编排函数: 顺序串四个维度 build,
    每维度 fail-safe 包裹(失败记 failed 继续不 abort),
    profile 非法时在任何 build 之前 abort。
  - _step_code/_step_database/_step_config/_step_corpus 是模块级函数,
    供 monkeypatch 替换——测试只验证编排逻辑,不跑真 JDT/Oracle/config/corpus。

Scoring / pass criteria:
  1. test_run_init_runs_four_dims_in_order:
     四维度按 code->database->config->corpus 顺序全部调用,verdict=ready。
  2. test_run_init_dimension_failure_degrades_not_aborts:
     code 抛 RuntimeError -> code.status=failed, 其余三维度仍运行,verdict=degraded。
  3. test_run_init_invalid_profile_aborts:
     _make_engine_and_validate 抛 ProfileValidationError -> steps=[], verdict=aborted。
  4. test_run_init_only_runs_single_dim:
     only="database" 仅触发 database step,其余不调用。

Test logic (automated):
  - 全部通过 monkeypatch 替换模块级 _step_* 和 _make_engine_and_validate;
    无 IO/网络/进程依赖。
  - _FakeProfile.projects 是真实 list(含 _FakeProj 实例),
    保证 profile.projects[0].path 可访问(run_init 在 repo_root=None 时求值)。
"""

from contextos.init import orchestrator
from contextos.init.report import StepResult


def _ok(dim):
    return StepResult(dimension=dim, status="ok", counts={})


class _FakeProj:
    path = "/tmp"


class _FakeProfile:
    projects = [_FakeProj()]

    class oracle:
        allowed_instances = ["A"]


def test_run_init_runs_four_dims_in_order(monkeypatch):
    calls = []
    monkeypatch.setattr(orchestrator, "_step_code", lambda p, e: calls.append("code") or _ok("code"))
    monkeypatch.setattr(orchestrator, "_step_database",
                        lambda p, e, now, repo_root, skip_oracle: calls.append("database") or _ok("database"))
    monkeypatch.setattr(orchestrator, "_step_config",
                        lambda p, e, repo_root, db_refreshed=None: calls.append("config") or _ok("config"))
    monkeypatch.setattr(orchestrator, "_step_corpus",
                        lambda p, e: calls.append("corpus") or _ok("corpus"))
    monkeypatch.setattr(orchestrator, "_make_engine_and_validate", lambda p: object())

    report = orchestrator.run_init(_FakeProfile(), now="2026-06-07T00:00:00")
    assert calls == ["code", "database", "config", "corpus"]
    assert report.verdict == "ready"


def test_run_init_dimension_failure_degrades_not_aborts(monkeypatch):
    monkeypatch.setattr(orchestrator, "_make_engine_and_validate", lambda p: object())
    monkeypatch.setattr(orchestrator, "_step_code",
                        lambda p, e: (_ for _ in ()).throw(RuntimeError("JDT boom")))
    monkeypatch.setattr(orchestrator, "_step_database",
                        lambda p, e, now, repo_root, skip_oracle: _ok("database"))
    monkeypatch.setattr(orchestrator, "_step_config", lambda p, e, repo_root, db_refreshed=None: _ok("config"))
    monkeypatch.setattr(orchestrator, "_step_corpus", lambda p, e: _ok("corpus"))

    report = orchestrator.run_init(_FakeProfile(), now="2026-06-07T00:00:00")
    code = [s for s in report.steps if s.dimension == "code"][0]
    assert code.status == "failed" and "JDT boom" in code.detail
    assert report.verdict == "degraded"                       # 不 abort, 其余仍跑
    assert {s.dimension for s in report.steps} == {"code", "database", "config", "corpus"}
    # reasons 是 CLI / serve-mcp 消费的契约: 失败维度必须出现在 reasons 里
    assert any(r.startswith("code:") and "JDT boom" in r for r in report.reasons)


def test_run_init_invalid_profile_aborts(monkeypatch):
    def _raise(p):
        from contextos.profile.validator import ProfileValidationError
        raise ProfileValidationError("bad")
    monkeypatch.setattr(orchestrator, "_make_engine_and_validate", _raise)
    report = orchestrator.run_init(_FakeProfile(), now="2026-06-07T00:00:00")
    assert report.verdict == "aborted" and report.steps == []


def test_run_init_only_runs_single_dim(monkeypatch):
    calls = []
    monkeypatch.setattr(orchestrator, "_make_engine_and_validate", lambda p: object())
    monkeypatch.setattr(orchestrator, "_step_code", lambda p, e: calls.append("code") or _ok("code"))
    monkeypatch.setattr(orchestrator, "_step_database",
                        lambda p, e, now, repo_root, skip_oracle: calls.append("database") or _ok("database"))
    monkeypatch.setattr(orchestrator, "_step_config", lambda p, e, repo_root: calls.append("config") or _ok("config"))
    monkeypatch.setattr(orchestrator, "_step_corpus", lambda p, e: calls.append("corpus") or _ok("corpus"))
    orchestrator.run_init(_FakeProfile(), now="2026-06-07T00:00:00", only="database")
    assert calls == ["database"]


def test_run_init_unknown_only_aborts(monkeypatch):
    # 未知 --only 维度名 -> abort, 不静默 no-op 假性 ready
    monkeypatch.setattr(orchestrator, "_make_engine_and_validate", lambda p: object())
    report = orchestrator.run_init(_FakeProfile(), now="2026-06-07T00:00:00", only="bogus")
    assert report.verdict == "aborted" and report.steps == []
    assert any("bogus" in r for r in report.reasons)


def test_run_init_threads_db_refreshed_false_when_database_degraded(monkeypatch):
    """MED-1: run_init 把'本次 database 维是否刷新成功'传给 _step_config。database degraded
    (skip_oracle / 连库降级)-> config 拿到 db_refreshed=False, 据此不靠旧快照谎报 ok。"""
    seen = {}
    monkeypatch.setattr(orchestrator, "_make_engine_and_validate", lambda p: object())
    monkeypatch.setattr(orchestrator, "_step_code", lambda p, e: _ok("code"))
    monkeypatch.setattr(orchestrator, "_step_database",
                        lambda p, e, now, repo_root, skip_oracle:
                        StepResult(dimension="database", status="degraded", counts={}))

    def _cfg(p, e, repo_root, db_refreshed=None):
        seen["db_refreshed"] = db_refreshed
        return _ok("config")

    monkeypatch.setattr(orchestrator, "_step_config", _cfg)
    monkeypatch.setattr(orchestrator, "_step_corpus", lambda p, e: _ok("corpus"))
    orchestrator.run_init(_FakeProfile(), now="2026-06-07T00:00:00")
    assert seen["db_refreshed"] is False


def test_run_init_threads_db_refreshed_true_when_database_ok(monkeypatch):
    """MED-1: database 维 ok(本次刷新成功)-> config 拿到 db_refreshed=True。"""
    seen = {}
    monkeypatch.setattr(orchestrator, "_make_engine_and_validate", lambda p: object())
    monkeypatch.setattr(orchestrator, "_step_code", lambda p, e: _ok("code"))
    monkeypatch.setattr(orchestrator, "_step_database",
                        lambda p, e, now, repo_root, skip_oracle: _ok("database"))

    def _cfg(p, e, repo_root, db_refreshed=None):
        seen["db_refreshed"] = db_refreshed
        return _ok("config")

    monkeypatch.setattr(orchestrator, "_step_config", _cfg)
    monkeypatch.setattr(orchestrator, "_step_corpus", lambda p, e: _ok("corpus"))
    orchestrator.run_init(_FakeProfile(), now="2026-06-07T00:00:00")
    assert seen["db_refreshed"] is True


def test_run_init_db_refreshed_none_when_only_config(monkeypatch):
    """MED-1: --only config 本次不跑 database 维 -> db_refreshed=None(读持久快照, 按 --only 设计)。"""
    seen = {}
    monkeypatch.setattr(orchestrator, "_make_engine_and_validate", lambda p: object())

    def _cfg(p, e, repo_root, db_refreshed="UNSET"):
        seen["db_refreshed"] = db_refreshed
        return _ok("config")

    monkeypatch.setattr(orchestrator, "_step_config", _cfg)
    orchestrator.run_init(_FakeProfile(), now="2026-06-07T00:00:00", only="config")
    assert seen["db_refreshed"] is None


def test_oracle_tables_from_store_groups_by_owner_table():
    # I1: config 维从 05 store 已装列元数据派 oracle_tables(零额外 Oracle 连接), 按 (owner,table) 聚合列
    from contextos.lineage import store
    from contextos.storage.db import make_engine
    e = make_engine("sqlite://")
    store.create_all(e)
    store.write_columns(e, [
        dict(owner="UPC", table_name="T_CFG", column_name="ID",
             data_type="X", nullable="N", comment="", column_id=1, db_name="A"),
        dict(owner="UPC", table_name="T_CFG", column_name="VAL",
             data_type="X", nullable="Y", comment="", column_id=2, db_name="A"),
        dict(owner="SEC", table_name="T_OTHER", column_name="C",
             data_type="X", nullable="Y", comment="", column_id=1, db_name="B"),
    ])
    out = orchestrator._oracle_tables_from_store(e)
    by_key = {(t["owner"], t["table"]): set(t["columns"]) for t in out}
    assert by_key[("UPC", "T_CFG")] == {"ID", "VAL"}
    assert by_key[("SEC", "T_OTHER")] == {"C"}


def test_oracle_tables_from_store_empty_when_no_metadata():
    from contextos.lineage import store
    from contextos.storage.db import make_engine
    e = make_engine("sqlite://")
    store.create_all(e)
    assert orchestrator._oracle_tables_from_store(e) == []


# ---------------------------------------------------------------------------
# Task 9: _augment_code_with_stopwords_draft — init code 维 best-effort 并入
# 停用词草稿(spec 附录 D6 / P2b)。
#
# Design intent:
#   - code 维 projection build 成功后, 顺手跑一趟停用词候选扫描, 写 gitignored 草稿
#     (contextos.recall.stop_keywords_gen.write_draft), 供人工核对后手动激活
#     stop_keywords_path。这是附带产物, 绝不新增 init 维度(_DIMS 仍是硬 4 元组),
#     也绝不能让草稿生成失败拖累 code 维本身的 status(P2b: best-effort fail-safe)。
#   - 直接测 helper 而不经 run_init/_step_code: 避免真跑 JDT(慢+需要 JDT LS 环境),
#     用真 Profile(而非既有 _FakeProfile stub, 它没有 storage/code/projects 真属性)
#     驱动 resolve_source_roots + write_draft 的真实路径。
#
# Scoring / pass criteria:
#   1. test_augment_success_adds_counts_and_detail: 正常路径 —— status 不变('ok'),
#      counts 新增 stop_kw_candidates 键(4 文件 < min_files=20 默认阈值, 值可以是 0,
#      只要求键存在、不是异常吞掉整个 counts), detail 挂草稿路径提示, 磁盘上草稿
#      文件真实写出。
#   2. test_augment_failure_does_not_degrade: write_draft 内部抛异常(monkeypatch 到
#      抛 RuntimeError) —— 断言 status 仍是外部传入的 'ok'(P2b 核心锁), detail 挂
#      失败告警文案而不是抛出/吞掉整个 StepResult。
#
# Test logic (automated):
#   - _profile_with_src 用最小合法字段集构造真 Profile(mirror _step_code 用到的
#     namespace: llm/embedding/reranker/query_expansion/storage/ingestion/
#     jdtls_runtime/oracle/code/projects), source_roots 指向 tmp 下含 4 个 .java
#     文件的目录, storage.data_dir 指向 tmp/database。
#   - monkeypatch 目标是 write_draft 在 contextos.recall.stop_keywords_gen 模块上的
#     属性(helper 内部用 `from ... import write_draft` 延迟到调用时求值, 所以 patch
#     模块属性对已导入的调用点生效)。
# ---------------------------------------------------------------------------

def _profile_with_src(tmp_path):
    """真 Profile: code.source_roots 指向含 .java 的 tmp 源树(resolve_source_roots + write_draft 真跑),
    data_dir=tmp/database。projects[0].path 也指该树。"""
    src = tmp_path / "proj"
    src.mkdir()
    for i in range(4):
        (src / f"F{i}.java").write_text("class F { FOOSVC x; }", encoding="utf-8")
    from contextos.profile.schema import Profile
    return Profile(**{
        "llm": {"provider": "fake", "api_key_env": "K"},
        "embedding": {"model": "BAAI/bge-m3"},
        "reranker": {"enabled": True, "model": "x", "top_k_input": 50, "top_k_output": 10},
        "query_expansion": {"enabled": True, "translation_provider": "main_llm", "fallback_provider": "x"},
        "storage": {"data_dir": str(tmp_path / "database")},
        "ingestion": {"default_cleanup": "full", "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "/j", "lombok_path": "/l", "java_home": "/h"},
        "oracle": {"tns_admin": "/t", "allowed_instances": ["TEST_DB1"]},
        "code": {"source_roots": [str(src)]},
        "projects": [{"name": "demoproj", "path": str(src), "language": "java", "build_system": "gradle"}],
    })


def test_augment_success_adds_counts_and_detail(tmp_path):
    from contextos.init.orchestrator import _augment_code_with_stopwords_draft
    from contextos.init.report import StepResult
    step = StepResult(dimension="code", status="ok", counts={"classes": 4}, detail="ok")
    out = _augment_code_with_stopwords_draft(step, _profile_with_src(tmp_path))
    assert out.status == "ok"                                    # 不改 status
    assert "stop_kw_candidates" in out.counts                    # counts 加候选数键(值可为 0, 小树不到阈值)
    assert "stop-keywords.draft.txt" in out.detail               # detail 挂草稿提示
    assert (tmp_path / "database" / "stop-keywords.draft.txt").exists()


def test_augment_failure_does_not_degrade(tmp_path, monkeypatch):
    """生成器抛 -> code step status 仍 ok, detail 挂告警(锁 spec P2b: best-effort 不降级)。"""
    from contextos.init.orchestrator import _augment_code_with_stopwords_draft
    from contextos.init.report import StepResult
    def _boom(*a, **k): raise RuntimeError("scan failed")
    # augment 内部 `from ... import write_draft` 在调用时求值, 故 patch 模块属性生效
    monkeypatch.setattr("contextos.recall.stop_keywords_gen.write_draft", _boom)
    step = StepResult(dimension="code", status="ok", counts={}, detail="ok")
    out = _augment_code_with_stopwords_draft(step, _profile_with_src(tmp_path))
    assert out.status == "ok"                                    # 不降级
    assert "草稿生成失败" in out.detail
