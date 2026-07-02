"""contextos init 顶层编排(spec §5/§6/§7): 顺序串四维度 + fail-safe 降级 + 实时日志 + InitReport。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal, cast

from contextos.init.report import InitReport, StepResult

log = logging.getLogger(__name__)

_DIMS = ("code", "database", "config", "corpus")


def _make_engine_and_validate(profile: Any) -> Any:
    """validate_profile(失败抛 -> run_init abort)+ engine_from_profile。"""
    from contextos.profile.validator import validate_profile
    from contextos.storage.db import engine_from_profile
    validate_profile(profile, check_paths=False)
    return engine_from_profile(profile)


_CODE_SANITY_TERMS = ("Service", "Impl", "Util", "Manager", "Exception")


def _code_sanity_symbol_count(searcher: Any) -> int:
    """workspaceSymbol 探活: 顺序查几个通用 Java 标识符, 命中即早退返计数。
    返 0 = 索引为空(JDT server 起了但项目 import 失败, 如 gradle 离线下载 distribution 失败)。
    任何非空 Java 工程, Service/Impl/Util/Manager/Exception 至少命中一个。"""
    total = 0
    for term in _CODE_SANITY_TERMS:
        try:
            total += len(searcher.request_workspace_symbol(term))
        except Exception:  # noqa: BLE001  探活查询失败(JDT 异常)当 0, 不致命
            pass
        if total:
            break
    return total


def _step_code(profile: Any, engine: Any) -> StepResult:
    """维度 code(spec §5.3, 04b T16 升级): JDT sanity 验证 + code_* 投影全量 build + 抽样对照。

    三段:
    1. 既有诚实性逻辑保留: JDT 起 + workspaceSymbol 探活(ServiceReady != 项目索引成功);
    2. 投影全量 build(04b): jar -> JSONL -> 单事务 staging 换新; jar 缺失等失败由
       build_projection 兜成 degraded(保旧 + detail 透传 README 重建指引), 不崩 init;
    3. 抽样对照(spec §3.1 条件 3): sanity ok 时把同一 JDT adapter 闭包进 sampler,
       staging 事务内对照投影 vs live workspaceSymbol, 超阈由 build 回滚保旧。
    """
    from contextos.code_intel.jdtls_provider.adapter import JdtlsAdapter
    from contextos.code_intel.jdtls_provider.config import (
        JdtlsRuntimeConfig, ProjectConfig, StorageConfig,
    )
    from contextos.code_intel.projection.build import build_projection
    from contextos.code_intel.projection.build_context import build_context_dict
    from contextos.code_intel.projection.incremental import head_commit_real
    from contextos.code_intel.projection.paths import indexer_jar
    from contextos.code_intel.projection.paths import repo_root as proj_repo_root
    from contextos.code_intel.projection.sample_check import sample_mismatch_ratio
    from contextos.storage.flock import try_lock
    log.info("=== 1/4 代码索引: JDT workspace import 验证(~110s 进行中)===")
    proj = profile.projects[0]
    project = ProjectConfig(name=proj.name, path=proj.path, language=proj.language,
                            build_system=proj.build_system,
                            java_settings=proj.java.model_dump() if proj.java else {})
    ws = profile.storage.jdtls_workspace_dir or str(
        Path(profile.storage.data_dir).expanduser() / "jdtls-workspaces")
    storage = StorageConfig(data_dir=str(Path(profile.storage.data_dir).expanduser()),
                            jdtls_workspace_dir=str(Path(ws).expanduser()))
    adapter = JdtlsAdapter(project=project, storage=storage,
                           runtime=JdtlsRuntimeConfig.from_profile(profile))
    # MED-3: start() 放 try 内, finally 保证 stop() 必被调用 —— start() spawn java 子进程后抛
    # (JDT 能力 assert / ServiceReady 超时等)时也杀进程, 防孤儿 JDT 进程泄漏整个 init 生命周期。
    # 不用 `with JdtlsAdapter(...)`: __enter__ 调 start(), start() 在 __enter__ 内抛时 Python
    # 不会调 __exit__, stop() 同样漏。stop() 内部已 try/except 且 self._ls=None 时 no-op。
    sanity = 0
    res: dict[str, Any] = {}
    try:
        adapter.start()                              # 失败抛 -> run_init 记 failed
        sanity = _code_sanity_symbol_count(adapter)  # 探活: JDT ServiceReady != 项目索引成功
        # 04b: 投影 build 必须在 adapter 存活期内跑 —— sampler 闭包复用同一 JDT 实例。
        log.info("=== 1/4 代码索引: code_* 投影全量 build ===")
        repo = proj_repo_root(profile)
        data_dir = Path(profile.storage.data_dir).expanduser()
        jar = indexer_jar(profile)     # NIT-1: 路径解析 chokepoint, 同 rebuild_entry
        ci = profile.code_index
        sampler: Any = None
        if sanity > 0 and ci.sample_check_classes + ci.sample_check_methods > 0:
            # sanity 失败(0 符号)不挂 sampler: 空索引对照必全 miss, 假阳回滚反而拦住换新。
            def _sampler(conn: Any) -> float:
                return sample_mismatch_ratio(
                    conn, adapter, n_classes=ci.sample_check_classes,
                    n_methods=ci.sample_check_methods)
            sampler = _sampler
        # MEDIUM-1(最终 review): 全量 build 与增量重建(rebuild_entry)共用同一把
        # projection.lock(data_dir 口径同 app_context.projection_lockfile)——
        # 否则 init 全量与 watcher/MCP 增量并发互踩 staging 事务。
        # 锁被持有 -> 诚实 degraded 不排队(spec §8 单飞); finally 仍会 stop() adapter。
        lockfile = data_dir / "projection.lock"
        with try_lock(lockfile) as got:
            if not got:
                return StepResult(
                    dimension="code", status="degraded",
                    counts={"workspace_imported": 1, "sanity_symbols": sanity},
                    detail="projection build skipped: another rebuild is running "
                           "(projection.lock held)")
            res = build_projection(
                engine=engine, repo_root=repo, java_home=profile.jdtls_runtime.java_home,
                jar=jar, xmx=ci.indexer_xmx, build_ctx=build_context_dict(profile),
                out_dir=data_dir / "code-index-out", indexed_commit=head_commit_real(repo),
                sampler=sampler, sample_max_mismatch=ci.sample_check_max_mismatch)
    finally:
        try:
            adapter.stop()
        except Exception:  # noqa: BLE001  防 finally 的 stop 异常吃掉 start 的真异常
            pass
    # 诚实性: ServiceReady 只说明 JDT server 起了; 项目 import 失败(如 gradle 离线下载 distribution
    # 失败)时 workspace 打开但 0 符号索引 -> 代码桥(04 workspaceSymbol)搜不到任何东西。此时不能
    # 凭'workspace 起来了'报 ok(否则 verdict=ready 掩盖代码桥不可用), 诚实标 degraded + 给修复指针。
    notes: list[str] = []
    if sanity <= 0:
        notes.append(
            "JDT workspace 已起但 0 符号索引(项目 import 可能失败, 如 gradle 离线下载 "
            "distribution 失败): 检查 [[projects]] 的 java gradle 配置 —— 删 gradle_version_"
            "override 改用本地 home install + gradle_arguments 加 --offline。代码桥 04 "
            "workspaceSymbol 此时搜不到符号。")
    build_detail = str(res.get("detail") or "")
    if build_detail:
        notes.append(build_detail)
    counts: dict[str, int] = {"workspace_imported": 1, "sanity_symbols": sanity}
    for k, v in (res.get("counts") or {}).items():    # 投影各表行数透传(degraded 无 counts)
        counts[str(k)] = int(v)
    status: Literal["ok", "degraded"] = (
        "ok" if (res.get("status") == "ok" and sanity > 0) else "degraded")
    return StepResult(dimension="code", status=status, counts=counts,
                      detail="; ".join(notes))


def _augment_code_with_stopwords_draft(step: StepResult, profile: Any) -> StepResult:
    """best-effort: code 维成功后并入停用词草稿(spec 附录 D6)。失败不降 status,只挂 detail 告警。"""
    try:
        from contextos.code_intel.projection.paths import resolve_source_roots
        from contextos.recall.stop_keywords_gen import write_draft
        roots = resolve_source_roots(profile)
        data_dir = Path(profile.storage.data_dir).expanduser()
        count, draft = write_draft(roots, exclude_dirs=profile.code.exclude_dirs, data_dir=data_dir)
        return step.model_copy(update={
            "counts": {**step.counts, "stop_kw_candidates": count},
            "detail": (step.detail + f" | 停用词草稿 {draft}({count} 候选), 核对后设 "
                       "stop_keywords_path 激活, 未激活不影响过滤").strip(" |"),
        })
    except Exception as exc:  # noqa: BLE001  草稿是附带产物, 失败不影响 code 维
        log.warning("停用词草稿生成失败(不影响 init): %s", exc)
        return step.model_copy(update={"detail": (step.detail + " | 草稿生成失败, 可稍后跑 "
                                                   "suggest-stop-keywords").strip(" |")})


def _step_database(profile: Any, engine: Any, now: str, repo_root: Path,
                   skip_oracle: bool) -> StepResult:
    from contextos.lineage.build_database import build_database_dimension
    log.info("=== 2/4 数据库维度 ===")
    out = build_database_dimension(profile, engine, now=now, repo_root=repo_root,
                                   skip_oracle=skip_oracle)
    status: Literal["ok", "degraded"] = (
        "ok" if out["oracle_status"] == "connected" else "degraded")
    counts = {"edges": int(out.get("lineage", {}).get("edges", 0)),
              "object_edges": int(out.get("object_lineage", {}).get("edges", 0)),
              "tables": int(out.get("metadata", {}).get("tables", 0))}
    return StepResult(dimension="database", status=status, counts=counts,
                      detail=out.get("detail", ""))


def _oracle_tables_from_store(engine: Any) -> list[dict[str, Any]]:
    """从 05 store 已装的列元数据(维度 2 build 的)聚合 oracle_tables 表清单, 供 config 维
    Phase B path A(表名 / 规则列启发)识别 DB 配置表 —— 零额外 Oracle 连接(spec §5.1
    '配置依赖维度 2 的 Oracle 表清单')。execute_query(path B DDL COMMENT)/ rag_search
    (path C/D)+ 基数信号(NUM_ROWS)留 Plan 06 后续, 见 spec §5.1 deferred 注。"""
    from contextos.lineage import store
    store.create_all(engine)          # idempotent: --only config(维度 2 未跑)时保 columns 表存在, 不崩
    by_table: dict[tuple[str, str], list[str]] = {}
    for r in store.all_columns(engine):
        owner = (r.get("owner") or "").strip()
        table = (r.get("table_name") or "").strip()
        if not owner or not table:
            continue
        cols = by_table.setdefault((owner, table), [])
        col = (r.get("column_name") or "").strip()
        if col:
            cols.append(col)
    return [{"owner": o, "table": t, "columns": cols} for (o, t), cols in by_table.items()]


def _step_config(profile: Any, engine: Any, repo_root: Path, *,
                 db_refreshed: bool | None = None) -> StepResult:
    """维度 config。db_refreshed = 本次 database 维是否刷新成功(MED-1 状态诚实):
      True  -> 本次刚刷新, oracle_tables 新鲜 -> ok
      False -> 本次跑了 database 但没刷新(skip_oracle/降级), oracle_tables 是旧快照 -> degraded
      None  -> 本次没跑 database 维(--only config), 读持久快照(按 --only 设计)-> ok 但 detail 注明
    """
    from contextos.config_dim import schema as cfg_schema
    from contextos.config_dim.pipeline import build_config_dimension
    from contextos.storage.migrate import ensure_schema
    log.info("=== 3/4 配置维度 ===")
    ensure_schema(engine, cfg_schema.metadata)    # config_dim 表自建 + 跨版本附加式补列(build_config_dimension 不建表, 对齐 smoke)
    cache_dir = Path(profile.storage.data_dir).expanduser() / "config-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    oracle_tables = _oracle_tables_from_store(engine)
    out = build_config_dimension(repo_root, profile, engine, cache_dir,
                                 oracle_tables=oracle_tables, engine_05=engine)
    status: Literal["ok", "degraded"]
    detail: str
    # 状态诚实(MED-1): 不能凭 store 是否有列(可能是上次旧快照)就判 ok。
    if not oracle_tables:
        # option A: oracle_tables 空有三种来源, 必须区分 —— 否则 lineage scope(连上但没抓列, 设计内)
        # 会被误报成'离线', 完美 init 永远 verdict≠ready(状态诚实红线的反向: 误报失败 + 错误原因)。
        if db_refreshed:
            # Oracle 连上且刷新成功, 但 lineage scope 默认不抓列 -> oracle_tables 空是设计内, 不是离线。
            # Phase A 文件配置已建; Phase B(DB 配置表识别)按 scope 跳过。诚实报 ok(完成了本 scope
            # 该做的), 列识别留 full scope opt-in(profile.tables.fetch_full_object_metadata=true)。
            status, detail = "ok", (
                "DB 配置表识别(Phase B)按 lineage scope 跳过(数据库维未抓列; 需要时置 "
                "profile.tables.fetch_full_object_metadata=true); 文件配置(Phase A)已建")
        elif db_refreshed is None:
            # --only config: 本次没跑数据库维, 且无持久列快照(lineage scope 默认不抓列)
            status, detail = "ok", (
                "未跑数据库维度且无持久列快照(lineage scope 默认不抓列); 文件配置(Phase A)已建")
        else:
            # db_refreshed is False: 本次跑了数据库维但 skip_oracle / 离线 / 刷新失败 -> 诚实 degraded
            status, detail = "degraded", (
                "无 Oracle 表元数据(skip_oracle / 离线 / 刷新失败)-> DB 配置表识别(Phase B)跳过; "
                "文件配置(Phase A)已建")
    elif db_refreshed is False:
        # 本次 database 维跑了但没刷新成功 -> oracle_tables 是旧快照, 不谎报 ok
        status, detail = "degraded", (
            "oracle_tables 来自旧快照(本次未刷新 Oracle), DB 配置表识别基于陈旧元数据; "
            "文件配置(Phase A)已建")
    elif db_refreshed is None:
        # --only config: 本次没跑数据库维, 读持久快照(按 --only 单维重跑设计)
        status, detail = "ok", "oracle_tables 来自持久快照(本次未跑数据库维度)"
    else:
        status, detail = "ok", ""
    return StepResult(dimension="config", status=status, detail=detail,
                      counts={"items": int(out.get("items", 0)),
                              "config_tables": int(out.get("config_tables", 0)),
                              "oracle_tables": len(oracle_tables)})


def _step_corpus(profile: Any, engine: Any) -> StepResult:
    from contextos.corpus.materialize import materialize_corpus
    from contextos.corpus.ocr import make_ocr
    from contextos.corpus.record_store import RecordStore
    log.info("=== 4/4 语料维度: materialize(sparse)===")
    notes: list[str] = []
    status: Literal["ok", "degraded"] = "ok"
    if getattr(profile.rag, "dense_enabled", False):
        notes.append("dense_enabled=true 但 dense 未实装(Plan 03.5); 本次只 sparse materialize, "
                     "serve 期 dense 查询会 NotImplementedError, 建议设 false")
        status = "degraded"
    materialized_dir = Path(profile.corpus.materialized_dir or
                            (Path(profile.storage.data_dir).expanduser() / "materialized"))
    materialized_dir.mkdir(parents=True, exist_ok=True)
    # spec Appendix C MUST: confirmed-cases 空目录也建出, contextos init 跑完即存在
    # (strict scope 不回退全量, 见 ops/paths.ensure_confirmed_cases_dir)。
    from contextos.ops.paths import ensure_confirmed_cases_dir
    ensure_confirmed_cases_dir(profile)
    rstore = RecordStore(engine)
    ocr = make_ocr(profile.corpus.ocr)
    out = materialize_corpus(sources=profile.corpus.sources, materialized_dir=materialized_dir,
                             store=rstore, ocr=ocr, backend_name=profile.corpus.ocr.backend)
    materialized = int(out.get("materialized", 0))
    skipped = int(out.get("skipped", 0))      # cache 命中(已物化, 算就绪)
    failed = int(out.get("failed", 0))
    deleted = int(out.get("deleted", 0))
    # MED-2: 失败/空必须诚实降级 + counts 暴露 failed/skipped, 不能只报 materialized 谎称 ok。
    # 全部失败(failed:N, materialized:0)与空语料(全 0)过去都显示 materialized:0 无法区分。
    if failed > 0:
        status = "degraded"
        notes.append(f"{failed} 个文档物化失败(详见 WARNING 日志)")
    elif materialized == 0 and skipped == 0:
        status = "degraded"
        notes.append("语料为空: 0 文档物化(RAG 证据维无可检索内容)")
    detail = "; ".join(notes)
    if status == "degraded":
        log.warning("  corpus degraded: %s", detail)
    return StepResult(dimension="corpus", status=status,
                      counts={"materialized": materialized, "skipped": skipped,
                              "failed": failed, "deleted": deleted},
                      detail=detail)


def run_init(profile: Any, *, now: str, repo_root: Any = None,
             only: str | None = None, skip_oracle: bool = False) -> InitReport:
    if only is not None and only not in _DIMS:
        # 未知 --only 维度名: 不静默 no-op(否则 verdict 假性 ready), 直接 abort
        reason = f"unknown dimension: {only} (expected one of {', '.join(_DIMS)})"
        log.error(reason)
        return InitReport(steps=[], verdict="aborted", reasons=[reason])

    try:
        engine = _make_engine_and_validate(profile)
    except Exception as exc:  # noqa: BLE001  profile 非法 -> abort
        log.error("profile 校验失败, 中止: %s", exc)
        return InitReport(steps=[], verdict="aborted", reasons=[str(exc)])

    repo = Path(repo_root) if repo_root else Path(profile.projects[0].path)
    wanted = [only] if only else list(_DIMS)
    # MED-1: config 维就绪判定要看'本次 database 维是否刷新成功', 不能凭 store 旧快照。
    # database 在本次跑 -> 默认 False(只有 database step ok 才置 True, 含 database 抛异常的情况);
    # 不在本次跑(--only config)-> None(读持久快照, 按 --only 单维重跑设计)。
    db_refreshed: bool | None = False if "database" in wanted else None
    steps: list[StepResult] = []
    for dim in wanted:
        try:
            if dim == "code":
                _cs = _step_code(profile, engine)
                _cs = _augment_code_with_stopwords_draft(_cs, profile)
                steps.append(_cs)
            elif dim == "database":
                db_step = _step_database(profile, engine, now, repo, skip_oracle)
                steps.append(db_step)
                db_refreshed = db_step.status == "ok"   # ok <=> oracle_status==connected <=> 本次刷新成功
            elif dim == "config":
                steps.append(_step_config(profile, engine, repo, db_refreshed=db_refreshed))
            elif dim == "corpus":
                steps.append(_step_corpus(profile, engine))
        except Exception as exc:  # noqa: BLE001  单维度失败 -> 记 failed, 继续不硬退
            # error + 全 traceback: 维度失败 = 数据缺失/陈旧, 需可诊断(detail 只存 type+msg)
            log.error("维度 %s 失败: %s", dim, exc, exc_info=True)
            _dim = cast(Literal["code", "database", "config", "corpus"], dim)
            steps.append(StepResult(dimension=_dim, status="failed",
                                    counts={}, detail=f"{type(exc).__name__}: {exc}"))

    reasons = [f"{s.dimension}: {s.detail or s.status}"
               for s in steps if s.status in ("degraded", "failed", "skipped")]
    verdict: Literal["ready", "degraded"] = (
        "ready" if all(s.status == "ok" for s in steps) else "degraded")
    return InitReport(steps=steps, verdict=verdict, reasons=reasons)
