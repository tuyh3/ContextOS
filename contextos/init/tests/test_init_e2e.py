"""Task 8 (Block 2): contextos init 端到端 smoke 测试(离线降级全栈)。

Design intent:
  - 验证 run_init 四维度全链路(code/database/config/corpus)能在纯离线环境下
    完成顺序编排,且降级时不崩溃(graceful degrade)。
  - code 维度 stub:避免真 JDT ~110s 进程;
    config/corpus 维度 stub:避免真仓库扫描 + materialize;
    database 维度真实运行(skip_oracle=True):仅建静态血缘,不连 Oracle。
  - 这是 Block 2 最终 e2e 回归:证明 Tasks 4-7 的 orchestrator/build_database/
    report/cli 全链路已接通。

Scoring / pass criteria:
  1. run_init 返回 InitReport,steps 包含全四维度。
  2. database 维度状态 = degraded(skip_oracle -> oracle_status=offline)。
  3. verdict = degraded(code stub 返回 degraded,至少一维不 ok)。
  4. 无异常抛出(fail-safe 守护有效)。

Test logic (automated):
  - 用 tmp_path 构建含 impl/Foo.sql 的最小 repo fixture,供 _step_database 真实运行。
  - Profile 用命名对象构造(与 test_build_database.py._profile 模式完全一致),
    避免 dict literal 导致 pyright reportArgumentType 错误。
  - 通过 monkeypatch 替换 _step_code/_step_config/_step_corpus;
    _step_database 不替换,让真实 build_database_dimension(skip_oracle=True) 跑。
  - storage.data_dir 设为 tmp_path/"data",确保 engine_from_profile 可创建工作目录。
"""
from __future__ import annotations

from pathlib import Path

from contextos.init import orchestrator
from contextos.init.report import StepResult
from contextos.profile.schema import (
    CodeConfig,
    DaoSqlPattern,
    EmbeddingConfig,
    IngestionConfig,
    JdtlsRuntimeConfig,
    LLMConfig,
    OracleConfig,
    Profile,
    ProjectConfig,
    QueryExpansionConfig,
    RerankerConfig,
    StorageConfig,
    TablesConfig,
)


def _build_repo(tmp_path: Path) -> None:
    """Create a minimal repo with one .sql file for static lineage to parse."""
    impl_dir = tmp_path / "impl"
    impl_dir.mkdir()
    (impl_dir / "Foo.sql").write_text("SELECT ID FROM T_X", encoding="utf-8")


def _profile(tmp_path: Path) -> Profile:
    """构造最小合法 Profile,与 test_build_database.py._profile 保持相同命名对象模式。

    差异点:
    - storage.data_dir 指向 tmp_path/data,确保 engine 可写
    - projects[0].path 指向 tmp_path,让 _step_database 能扫到 impl/Foo.sql
    """
    data_dir = str(tmp_path / "data")
    return Profile(
        llm=LLMConfig(provider="x", api_key_env="X"),
        embedding=EmbeddingConfig(model="m"),
        reranker=RerankerConfig(model="m"),
        query_expansion=QueryExpansionConfig(translation_provider="p", fallback_provider="p"),
        storage=StorageConfig(data_dir=data_dir),
        ingestion=IngestionConfig(),
        jdtls_runtime=JdtlsRuntimeConfig(jdtls_path="/j", lombok_path="/l", java_home="/h"),
        oracle=OracleConfig(tns_admin="/tns", allowed_instances=["A"]),
        code=CodeConfig(dao_sql_patterns=[DaoSqlPattern(path_contains=["/impl/"])]),
        tables=TablesConfig(),
        projects=[ProjectConfig(name="p", path=str(tmp_path), language="java")],
    )


def test_init_e2e_offline_degrades_gracefully(monkeypatch, tmp_path):
    """端到端: skip_oracle + code 维 stub(不起真 JDT)-> 跑完四维度, verdict degraded 不崩。"""
    _build_repo(tmp_path)
    prof = _profile(tmp_path)

    # code 维 stub: 避免真 JDT ~110s 进程; 返回 degraded 以验证 verdict 聚合逻辑
    monkeypatch.setattr(
        orchestrator, "_step_code",
        lambda p, e: StepResult(
            dimension="code", status="degraded",
            counts={}, detail="JDT 环境未配(e2e stub)"
        ),
    )
    # config 维 stub: 避免真仓库扫描
    monkeypatch.setattr(
        orchestrator, "_step_config",
        lambda p, e, repo_root, db_refreshed=None: StepResult(
            dimension="config", status="ok", counts={"items": 0}
        ),
    )
    # corpus 维 stub: 避免真 materialize
    monkeypatch.setattr(
        orchestrator, "_step_corpus",
        lambda p, e: StepResult(
            dimension="corpus", status="ok", counts={"materialized": 0}
        ),
    )
    # _step_database 不 stub: 真实运行 build_database_dimension(skip_oracle=True)

    report = orchestrator.run_init(
        prof,
        now="2026-06-07T00:00:00",
        repo_root=tmp_path,
        skip_oracle=True,
    )

    # 四维度全部执行(顺序编排不 short-circuit)
    assert {s.dimension for s in report.steps} == {"code", "database", "config", "corpus"}

    # database 维: skip_oracle -> oracle_status=offline -> status=degraded
    db_step = next(s for s in report.steps if s.dimension == "database")
    assert db_step.status == "degraded", (
        f"expected database.status=degraded (skip_oracle), got {db_step.status!r}: {db_step.detail}"
    )

    # code 维: stub 返回 degraded -> 聚合 verdict=degraded
    assert report.verdict == "degraded", (
        f"expected verdict=degraded (code stub degraded), got {report.verdict!r}"
    )

    # 降级维度必须出现在 reasons(CLI / MCP 消费的契约)。reasons 格式 = f"{dim}: {detail|status}",
    # 用 startswith 锚定维度前缀, 不靠 split(":") 解析(detail 里也可能含冒号)
    assert any(r.startswith("code:") for r in report.reasons), f"code 不在 reasons: {report.reasons}"
    assert any(r.startswith("database:") for r in report.reasons), f"database 不在 reasons: {report.reasons}"


def test_step_config_degraded_when_skip_oracle_no_metadata(tmp_path):
    """I1: skip_oracle/离线(db_refreshed=False)且无列元数据 -> config Phase B 无表清单空转,
    诚实标 degraded, oracle_tables=0(不再谎报 ok)。

    注意(option A): db_refreshed=False 才代表 skip_oracle/离线/刷新失败; db_refreshed=True 但
    columns 空 = lineage scope 设计内(连上了没抓列), 那是 ok 不是 degraded(见下个测试)。"""
    from contextos.lineage import store
    from contextos.storage.db import engine_from_profile
    _build_repo(tmp_path)
    prof = _profile(tmp_path)
    engine = engine_from_profile(prof)
    store.create_all(engine)                      # 模拟维度 2 已建表但未装列元数据(skip_oracle)
    result = orchestrator._step_config(prof, engine, tmp_path, db_refreshed=False)
    assert result.status == "degraded"
    assert result.counts["oracle_tables"] == 0


def test_step_config_ok_when_lineage_scope_no_columns(tmp_path):
    """option A: Oracle 连上且刷新成功(db_refreshed=True)但 columns 空(lineage scope 默认不抓列)
    -> config 诚实标 ok(完成了本 scope 该做的: Phase A 文件配置; Phase B 列识别按设计跳过),
    detail 说明是 lineage scope 跳过, 不能误报'离线/skip_oracle'(否则完美 init 永远 verdict≠ready)。"""
    from contextos.lineage import store
    from contextos.storage.db import engine_from_profile
    _build_repo(tmp_path)
    prof = _profile(tmp_path)
    engine = engine_from_profile(prof)
    store.create_all(engine)                      # 维度 2 lineage scope: 建表但 columns 表空
    result = orchestrator._step_config(prof, engine, tmp_path, db_refreshed=True)
    assert result.status == "ok"                  # 不是 degraded
    assert result.counts["oracle_tables"] == 0
    assert "lineage scope" in result.detail        # 诚实说明是 scope 跳过
    assert "离线" not in result.detail and "skip_oracle" not in result.detail   # 不误报离线


def test_step_config_ok_when_oracle_metadata_present(tmp_path):
    """I1: 维度 2 已装 Oracle 列元数据 -> config 从 store 派 oracle_tables, Phase B path A 跑,
    标 ok, oracle_tables>0(spec §5.1 '配置依赖维度 2 表清单')。"""
    from contextos.lineage import store
    from contextos.storage.db import engine_from_profile
    _build_repo(tmp_path)
    prof = _profile(tmp_path)
    engine = engine_from_profile(prof)
    store.create_all(engine)
    store.write_columns(engine, [
        dict(owner="UPC", table_name="T_PARAM", column_name="ID",
             data_type="X", nullable="N", comment="", column_id=1, db_name="A"),
        dict(owner="UPC", table_name="T_PARAM", column_name="EFFECTIVE_DATE",
             data_type="DATE", nullable="Y", comment="", column_id=2, db_name="A"),
    ])
    result = orchestrator._step_config(prof, engine, tmp_path, db_refreshed=True)
    assert result.status == "ok"
    assert result.counts["oracle_tables"] >= 1


def test_step_config_degraded_when_db_not_refreshed_this_run(tmp_path):
    """MED-1: 本次未刷新 Oracle 元数据(skip_oracle / database 维 degraded)时, 即便 store 里
    有上次的旧列, config 不能凭旧快照谎报 ok -> 必须 degraded 且 detail 注明基于旧快照。

    设计思路(memory feedback_contextos_test_documentation):
    - 复现 MED-1: _step_config 原 status 仅看 store 是否有列(持久快照), 与本次是否连/刷新
      Oracle 无关; skip_oracle 重跑会基于陈旧列谎报 ok。
    - db_refreshed=False 表示本次 database 维跑了但没刷新成功(skip_oracle/降级)。
    - 评分: status==degraded 且 detail 含'旧快照'或'未刷新'; oracle_tables 计数仍如实给出。
    """
    from contextos.lineage import store
    from contextos.storage.db import engine_from_profile
    _build_repo(tmp_path)
    prof = _profile(tmp_path)
    engine = engine_from_profile(prof)
    store.create_all(engine)
    store.write_columns(engine, [
        dict(owner="OWNER_X", table_name="T_CFG", column_name="ID",
             data_type="X", nullable="N", comment="", column_id=1, db_name="A")])
    result = orchestrator._step_config(prof, engine, tmp_path, db_refreshed=False)
    assert result.status == "degraded"
    assert "旧快照" in result.detail or "未刷新" in result.detail


def test_step_code_calls_stop_even_when_start_raises(tmp_path, monkeypatch):
    """MED-3: adapter.start() 抛(子进程可能已 spawn)时 _step_code 必须仍调 stop() 杀进程防孤儿。

    设计思路(memory feedback_contextos_test_documentation):
    - 复现 MED-3: 原 _step_code 把 adapter.start() 写在 try 外, 只有 stop() 被 try 包。start()
      在 spawn java 子进程之后抛(JDT 能力 assert / ServiceReady 超时等), 异常直接穿出, stop()
      永不调用 -> 孤儿 JDT java 进程(GB 级)泄漏整个 init 生命周期。
    - 注: 不能用 `with JdtlsAdapter(...)` context manager 修 —— __enter__ 调 start(), start()
      在 __enter__ 内抛时 Python 不会调 __exit__, stop() 同样漏。正解 = 显式 try/finally。
    - 评分: start() 抛 RuntimeError -> _step_code 仍把异常抛出(run_init 记 failed)且 stop() 被调用。
    - 自动逻辑: monkeypatch JdtlsAdapter 成 fake(start 抛 + 记 stop 是否被调), 离线无真 JDT。
    """
    import contextos.code_intel.jdtls_provider.adapter as adapter_mod
    stop_called = {"v": False}

    class _FakeAdapter:
        def __init__(self, **kw):
            pass

        def start(self, *a, **k):
            raise RuntimeError("JDT import boom after spawn")

        def stop(self):
            stop_called["v"] = True

    monkeypatch.setattr(adapter_mod, "JdtlsAdapter", _FakeAdapter)
    prof = _profile(tmp_path)
    import pytest
    with pytest.raises(RuntimeError):
        orchestrator._step_code(prof, object())   # engine 未触达(start 先抛)
    assert stop_called["v"] is True       # start 抛也调了 stop(防孤儿进程)


def _corpus_profile(tmp_path) -> Profile:
    return _profile(tmp_path)


def test_step_corpus_degraded_and_counts_when_failures(tmp_path, monkeypatch):
    """MED-2: materialize 有失败(failed>0)-> _step_corpus degraded 且 counts 暴露 failed,
    不能只报 materialized 谎称 ok。全部失败(failed:N, materialized:0)与空语料必须可区分。"""
    import contextos.corpus.materialize as mat
    from contextos.storage.db import engine_from_profile
    prof = _corpus_profile(tmp_path)
    engine = engine_from_profile(prof)
    monkeypatch.setattr(mat, "materialize_corpus",
                        lambda **kw: {"materialized": 2, "skipped": 0, "failed": 3, "deleted": 0})
    result = orchestrator._step_corpus(prof, engine)
    assert result.status == "degraded"
    assert result.counts["failed"] == 3
    assert result.counts["materialized"] == 2


def test_step_corpus_ok_when_all_cached(tmp_path, monkeypatch):
    """MED-2: 全 cache 命中(skipped>0, materialized=0, failed=0)-> ok(已物化过, 非空非失败)。"""
    import contextos.corpus.materialize as mat
    from contextos.storage.db import engine_from_profile
    prof = _corpus_profile(tmp_path)
    engine = engine_from_profile(prof)
    monkeypatch.setattr(mat, "materialize_corpus",
                        lambda **kw: {"materialized": 0, "skipped": 5, "failed": 0, "deleted": 0})
    result = orchestrator._step_corpus(prof, engine)
    assert result.status == "ok"
    assert result.counts["skipped"] == 5


def test_step_corpus_degraded_when_empty(tmp_path, monkeypatch):
    """MED-2: 0 文档(materialized=skipped=0, failed=0)-> degraded(RAG 维无内容, 别静默当 ok)。"""
    import contextos.corpus.materialize as mat
    from contextos.storage.db import engine_from_profile
    prof = _corpus_profile(tmp_path)
    engine = engine_from_profile(prof)
    monkeypatch.setattr(mat, "materialize_corpus",
                        lambda **kw: {"materialized": 0, "skipped": 0, "failed": 0, "deleted": 0})
    result = orchestrator._step_corpus(prof, engine)
    assert result.status == "degraded"


# --- 2026-06-08: _step_code 谎报修复(JDT ServiceReady != 项目索引成功)---
# 真实踩过: gradle 离线下载 distribution 失败 -> 项目 import 失败 -> workspace 打开但 0 符号
# 索引 -> 代码桥(04 workspaceSymbol)搜不到任何东西, 但 _step_code 凭 ServiceReady 报 ok ->
# verdict=ready 掩盖代码桥不可用。修: start() 后 workspaceSymbol 探活, 0 符号则诚实 degraded。

def test_code_sanity_symbol_count_zero_and_nonzero():
    """探活辅助: 顺序查通用 Java 标识符, 命中即早退返计数; 全空返 0。"""
    class _Empty:
        def request_workspace_symbol(self, q): return []

    class _Has:
        def request_workspace_symbol(self, q):
            return [{"name": "FooService"}, {"name": "BarService"}] if q == "Service" else []

    assert orchestrator._code_sanity_symbol_count(_Empty()) == 0
    assert orchestrator._code_sanity_symbol_count(_Has()) == 2   # "Service" 命中即早退


def _patch_fake_jdt(monkeypatch, hits: list):
    """把 JdtlsAdapter 换成 fake(不起真 JDT), request_workspace_symbol 返 hits。"""
    class _FakeAdapter:
        def __init__(self, *a, **k): pass
        def start(self, *a, **k): pass
        def stop(self, *a, **k): pass
        def request_workspace_symbol(self, q): return hits
    monkeypatch.setattr(
        "contextos.code_intel.jdtls_provider.adapter.JdtlsAdapter", _FakeAdapter)


def _patch_fake_build(monkeypatch, seen: dict | None = None,
                      counts: dict | None = None):
    """把 build_projection 换成 fake(不跑真 jar)。monkeypatch 在 build 模块上:
    _step_code 是函数内 `from ...build import build_projection`, 调用时才取属性。"""
    import contextos.code_intel.projection.build as build_mod

    def _fake(**kw):
        if seen is not None:
            seen.update(kw)
        return {"status": "ok", "detail": "", "build_id": "b1",
                "counts": dict(counts or {})}

    monkeypatch.setattr(build_mod, "build_projection", _fake)


def test_step_code_degraded_when_zero_symbols(monkeypatch, tmp_path):
    """JDT 起来了但 0 符号(项目 import 失败如 gradle 离线)-> 诚实 degraded(投影 build ok
    也不能掩盖代码桥不可用), 且不挂 sampler(0 符号的 JDT 对照无意义, 全 miss 假阳回滚)。"""
    from contextos.storage.db import engine_from_profile
    _patch_fake_jdt(monkeypatch, [])
    seen: dict = {}
    _patch_fake_build(monkeypatch, seen=seen)
    prof = _profile(tmp_path)
    res = orchestrator._step_code(prof, engine_from_profile(prof))
    assert res.status == "degraded"
    assert res.counts.get("sanity_symbols") == 0
    assert "gradle" in res.detail                      # 带可诊断 reason(gradle 配置提示)
    assert seen["sampler"] is None                     # sanity 失败不抽样对照


def test_step_code_ok_when_symbols_indexed(monkeypatch, tmp_path):
    """JDT 起来 + workspaceSymbol 真返符号 + 投影 build ok -> ok + 暴露 sanity_symbols 计数。"""
    from contextos.storage.db import engine_from_profile
    _patch_fake_jdt(monkeypatch, [{"name": "CustService"}])
    _patch_fake_build(monkeypatch)
    prof = _profile(tmp_path)
    res = orchestrator._step_code(prof, engine_from_profile(prof))
    assert res.status == "ok"
    assert res.counts.get("sanity_symbols", 0) >= 1


# --- T16(04b): _step_code 升级 = JDT sanity + code_* 投影全量 build + 抽样对照 ---

def test_step_code_jar_missing_degrades_with_rebuild_pointer(monkeypatch, tmp_path):
    """jar 缺失 -> build_projection 内 IndexerError(消息含 vendor/java-indexer README 重建
    指引)-> degraded + detail 透传, **不崩 init**; JDT sanity 信息仍保留在 counts。
    build_projection 真跑(不 stub): 验证失败被兜成 degraded 而非异常逃逸。"""
    from contextos.profile.schema import CodeIndexConfig
    from contextos.storage.db import engine_from_profile
    _patch_fake_jdt(monkeypatch, [{"name": "FooService"}])
    prof = _profile(tmp_path).model_copy(update={
        "code_index": CodeIndexConfig(indexer_jar=str(tmp_path / "absent" / "indexer.jar"))})
    res = orchestrator._step_code(prof, engine_from_profile(prof))
    assert res.status == "degraded"
    assert "java-indexer" in res.detail                # README 重建指引透传
    assert res.counts.get("sanity_symbols", 0) >= 1


def test_step_code_build_ok_passes_counts_and_sampler(monkeypatch, tmp_path):
    """build 成功路径: res counts 透传进 StepResult.counts; sanity ok 且 sample_check_* > 0
    -> sampler 闭包注入(staging 事务内对照, 复用同一 JDT adapter)+ 阈值来自 profile。"""
    from contextos.storage.db import engine_from_profile
    _patch_fake_jdt(monkeypatch, [{"name": "FooService"}])
    seen: dict = {}
    _patch_fake_build(monkeypatch, seen=seen, counts={"code_classes": 7, "code_methods": 21})
    prof = _profile(tmp_path)
    engine = engine_from_profile(prof)
    res = orchestrator._step_code(prof, engine)
    assert res.status == "ok"
    assert res.counts["code_classes"] == 7 and res.counts["code_methods"] == 21
    assert res.counts.get("sanity_symbols", 0) >= 1
    assert seen["engine"] is engine
    assert seen["sampler"] is not None                 # 默认 sample_check 50+100 > 0
    assert seen["sample_max_mismatch"] == prof.code_index.sample_check_max_mismatch


def test_step_code_skips_build_when_projection_lock_held(monkeypatch, tmp_path):
    """MEDIUM-1(最终 review): init 全量 build 必须拿 projection.lock(与 rebuild_entry
    增量同一把锁, data_dir/projection.lock 口径)—— 否则 init 全量与 watcher/MCP 增量
    并发互踩 staging 事务。锁被别人持有 -> 诚实 degraded + build_projection 不被调,
    不排队阻塞(spec §8 单飞)。

    评分: 测试先持锁再跑 _step_code -> status=degraded + detail 含 lock 语义 +
    build_projection 调用计数 = 0; sanity counts 仍保留(JDT 探活照跑)。"""
    from contextos.storage.db import engine_from_profile
    from contextos.storage.flock import try_lock
    _patch_fake_jdt(monkeypatch, [{"name": "FooService"}])
    seen: dict = {}
    _patch_fake_build(monkeypatch, seen=seen)
    prof = _profile(tmp_path)
    lockfile = Path(prof.storage.data_dir).expanduser() / "projection.lock"
    with try_lock(lockfile) as got:
        assert got
        res = orchestrator._step_code(prof, engine_from_profile(prof))
    assert res.status == "degraded"
    assert "lock" in res.detail or "running" in res.detail
    assert not seen                                # build_projection 未被调
    assert res.counts.get("sanity_symbols", 0) >= 1   # sanity 信息保留
