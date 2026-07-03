"""3 元工具(Plan 10 Task 8):health_check / profile_info / incremental_rebuild。

本层是**薄包装**(spec §设计单元边界):只读 app_ctx 已有资源的状态 + profile 的非敏感
元信息,不写查询逻辑、不强行起重资源。register_meta_tools(mcp, app_ctx) 把三者注册成
MCP tool(@mcp.tool()),异常转 ToolError(不裸传 traceback 给不可信 host,红线 #9)。

三个工具
--------
1. health_check(app_ctx) -> {jdt_ls, oracle, models, engine, code_projection, ripgrep,
   jdtls_runtime}
   各探测独立 try/except, 任一失败只把该字段标成 error 字符串, **绝不冒泡**(运维要能在
   任意半 down 态下拿到一张体检表)。关键设计:
   - jdt_ls / models 用"资源是否已 materialized"判 cold/lazy vs ready —— AppContext 的
     searcher / llm 是 cached_property, 首次访问才构造。**不**在 health_check 里访问它们
     (访问 llm 在离线缺凭据时会抛 LLMConfigError;searcher 自 04b 起是 ProjectionSearcher,
     构造廉价零 JDT —— "不访问"的原始动机[JDT ~196s 冷启]已不在, 但语义保持"是否已被用过")。
     `jdt_ls` 字段名是历史口径(API 面不改), 实探投影查询器;JDT 本体仅 init/build 期存在。
     探测 app_ctx.__dict__ 里有没有缓存键(cached_property 命中后落 __dict__[<name>]),
     有 = 已被用过 = "ready", 无 = "cold"(jdt_ls)/"lazy"(models)。非破坏性探测。
   - oracle 用 app_ctx.oracle_router().fan_out() 是否非空判 connected/offline(Block 1b;
     router=None 或 fan_out=[] 均视作离线, 这里再包一层防御)。
   - engine 轻量 SELECT 1 探活(SQLAlchemy engine 构造廉价、首次 connect 才真连);成功 "ok",
     异常 "error: <msg>"。
   - code_projection(Plan 04b T14)读 code_projection_meta:未 build -> not_built + hint;
     已 build -> {status, build_id, indexed_commit[, commits_behind]}(spec §9 freshness)。

2. profile_info(app_ctx) -> {profile_path, data_dir, repo_root, source_roots,
   oracle_instances, rag_corpora, missing_required}
   **脱敏铁律(红线 #9 host 不可信 + 凭据绝不外泄)**:本工具**白名单输出** —— 只产被显式
   选中的非敏感字段(实例名 / corpus 子集名 / 路径 / 缺失必填项清单),**绝不**做整 profile
   model_dump(从结构上杜绝任何凭据值、自由文本里的 password/secret/token 形态字符串混入返回)。
   profile.llm.api_key_env 是环境变量**名**(指针, 非密钥本体), 本工具也不回显它的值 —— 更不
   读 os.environ[api_key_env]。data_dir / profile_path 是路径(非凭据), 正常列出。

3. incremental_rebuild(app_ctx, *, scope="all")
   code scope 实装(Plan 04b T14):走 rebuild_entry.incremental_rebuild_code(flock 单飞,
   别人持锁 -> already_running;撞阈值在同一持锁块内接全量重建)。scope="all" 当前等价
   code;其余 scope(rag/lineage/config 等)仍占位 not_implemented(维度增量 v1.x)。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError
from sqlalchemy import text

if TYPE_CHECKING:
    from fastmcp import FastMCP


# --------------------------------------------------------------------------- health_check


def health_check_impl(app_ctx: Any) -> dict[str, Any]:
    """{jdt_ls, oracle, models, engine, code_projection, ripgrep, jdtls_runtime} 各状态。
    每个探测 try/except, 绝不冒泡。"""
    return {
        "jdt_ls": _probe_jdt_ls(app_ctx),
        "oracle": _probe_oracle(app_ctx),
        "models": _probe_models(app_ctx),
        "engine": _probe_engine(app_ctx),
        "code_projection": _probe_code_projection(app_ctx),
        "ripgrep": _probe_ripgrep(app_ctx),
        "jdtls_runtime": _probe_jdtls_runtime(app_ctx),
    }


def _probe_jdtls_runtime(app_ctx: Any) -> dict[str, Any]:
    """profile [jdtls_runtime] 三路径存在性 + 深校验(spec C4: launcher jar / 平台
    config / lombok 是文件 / java_home 有 bin/java —— 治"浅 exists 假 ok, 照抄后
    init 才炸"); 缺失时按 spec C1 顺序探测建议: 先 <cwd>/runtime/contextos-runtime
    官方 bundle(suggestion 四行含 indexer_jar, 全绝对路径, spec C2), 探不到再本机
    VSCode redhat.java 扩展(三行), 都没有给安装指引。profile 三路径 ok 时仍单独查
    code_index.indexer_jar(C1 反遮蔽: 用 VSCode 扩展的用户也要收到 bundle 里现成的
    java-indexer.jar 建议, 否则继续撞"自己 mvn build"的旧摩擦)。
    只探不写(回写 human-gated); 返回 dict 非 str, 因为 suggestion 是结构化内容。"""
    try:
        r = app_ctx.profile.jdtls_runtime
        missing = [
            name for name, raw in (
                ("jdtls_path", r.jdtls_path),
                ("lombok_path", r.lombok_path),
                ("java_home", r.java_home),
            ) if not Path(raw).expanduser().exists()
        ]
        if not missing:
            missing = _deep_validate_profile_runtime(r)
        if not missing:
            out: dict[str, Any] = {"status": "ok"}
            _maybe_suggest_indexer_jar(app_ctx, out)
            return out
        from contextos.code_intel.jdtls_provider.discovery import (
            discover_runtime_bundle,
            discover_vscode_jdtls,
        )
        out = {"status": "missing", "missing": missing}
        bundle = discover_runtime_bundle()
        if bundle is not None:
            out["suggestion"] = {
                "jdtls_path": bundle.jdtls_path,
                "lombok_path": bundle.lombok_path,
                "java_home": bundle.java_home,
                "indexer_jar": bundle.indexer_jar,
                "source": bundle.source,
            }
            out["hint"] = (
                "探到 runtime bundle(runtime/contextos-runtime), 把 suggestion 前三路径"
                "填进 profile [jdtls_runtime], indexer_jar 填进 [code_index].indexer_jar"
                "(全绝对路径, TOML 里 Windows 路径用正斜杠)"
            )
            return out
        found = discover_vscode_jdtls()
        if found is not None:
            out["suggestion"] = {
                "jdtls_path": found.jdtls_path,
                "lombok_path": found.lombok_path,
                "java_home": found.java_home,
                "source": found.source,
            }
            out["hint"] = (
                "本机探到 VSCode Java 扩展, 把 suggestion 三路径填进 profile "
                "[jdtls_runtime](TOML 里 Windows 路径用正斜杠)"
            )
        else:
            out["hint"] = (
                "本机未探到 runtime bundle 或 VSCode redhat.java 扩展; 安装/下载指引见 "
                "README 'JDT LS 运行时从哪来'"
            )
        return out
    except Exception as exc:
        return {"status": f"error: {exc}"}


def _deep_validate_profile_runtime(r: Any) -> list[str]:
    """三路径都 exists 后的深校验(spec C4 顺带条款: profile 支路同款判据升级)。

    返回缺陷清单(空 = 通过); 条目带"(深校验)"标记与浅 missing 区分, 用户能看懂
    是"路径在但内容残缺"而非填错路径。三条腿: jdtls 目录要有 launcher jar + 当前
    平台 config_*(validate_jdtls_layout); lombok 要是文件非目录; java_home 下要有
    bin/java(win 为 java.exe)—— 全是 JDT LS 真实启动的硬前提。"""
    from contextos.code_intel.jdtls_provider.discovery import validate_jdtls_layout

    problems: list[str] = []
    reason = validate_jdtls_layout(Path(r.jdtls_path).expanduser())
    if reason is not None:
        problems.append(f"jdtls_path(深校验): {reason}")
    if not Path(r.lombok_path).expanduser().is_file():
        problems.append("lombok_path(深校验): 不是常规 jar 文件")
    java = Path(r.java_home).expanduser() / "bin" / (
        "java.exe" if sys.platform == "win32" else "java")
    if not java.is_file():
        problems.append(f"java_home(深校验): 缺 bin/{java.name} 可执行")
    return problems


def _maybe_suggest_indexer_jar(app_ctx: Any, out: dict[str, Any]) -> None:
    """spec C1 反遮蔽: profile 三路径 ok 也单独查 code_index.indexer_jar —— 默认值指
    vendor gitignored 路径, 用 VSCode 扩展的用户没有 bundle suggestion 入口, 这行
    补充建议是他们唯一能收到现成 indexer 的地方。best-effort: 任何异常吞掉,
    绝不把 ok 拖成 error(补充建议非判定;jar 解析走 projection/paths chokepoint)。"""
    try:
        from contextos.code_intel.jdtls_provider.discovery import discover_runtime_bundle
        from contextos.code_intel.projection.paths import indexer_jar as _resolve_indexer_jar

        if _resolve_indexer_jar(app_ctx.profile).exists():
            return
        bundle = discover_runtime_bundle()
        if bundle is not None:
            out["indexer_jar_suggestion"] = bundle.indexer_jar
            out["hint"] = (
                "code_index.indexer_jar 指向的 jar 不存在, 但 runtime bundle 里有现成的; "
                "把 indexer_jar_suggestion 填进 profile [code_index].indexer_jar(绝对路径)"
            )
    except Exception:
        pass


def _probe_jdt_ls(app_ctx: Any) -> str:
    """searcher 已构造 -> 'ready';否则 'cold'。异常 -> 'error'。字段名 jdt_ls 是历史口径
    (API 面不改): 04b 后 searcher=ProjectionSearcher(零 JDT), 实探投影查询器。"""
    try:
        # cached_property 命中后落 instance.__dict__['searcher'];检查缓存键不触发构造。
        return "ready" if "searcher" in getattr(app_ctx, "__dict__", {}) else "cold"
    except Exception as exc:  # 极防御:__dict__ 访问理论不抛, 仍兜底
        return f"error: {exc}"


def _probe_models(app_ctx: Any) -> str:
    """llm 已构造 -> 'ready';否则 'lazy'(离线缺凭据时构造会抛, 故只探缓存键)。"""
    try:
        return "ready" if "llm" in getattr(app_ctx, "__dict__", {}) else "lazy"
    except Exception as exc:
        return f"error: {exc}"


def _probe_oracle(app_ctx: Any) -> str:
    """oracle_router().fan_out() 有非空结果 -> 'connected', 否则 'offline'。异常 -> 'offline'(降级)。

    Block 1b: 使用 DbRouter.fan_out() 探活(多库支持);router 内部降级不崩(nil-safe)。
    """
    try:
        router = app_ctx.oracle_router()
        connected = bool(router and router.fan_out())
        return "connected" if connected else "offline"
    except Exception:
        # router 本应自降级;任何异常一律视作离线, 不把 health_check 拖崩。
        return "offline"


def _probe_engine(app_ctx: Any) -> str:
    """engine SELECT 1 轻量探活 -> 'ok';任何异常 -> 'error: <msg>'。"""
    try:
        engine = app_ctx.engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        return f"error: {exc}"


def _probe_ripgrep(app_ctx: Any) -> str:
    """rg 在 PATH 且可运行 -> 'ok';否则 'missing'(search_source 会硬抛 ToolError + 装 rg 提示)。"""
    try:
        import shutil
        from contextos.util.subproc_text import run_rg
        if shutil.which("rg") is None:
            return "missing"
        proc = run_rg(["--version"], timeout=5)
        return "ok" if proc.returncode == 0 else "missing"
    except Exception:
        return "missing"


def _probe_code_projection(app_ctx: Any) -> dict[str, Any]:
    """投影状态(spec §9 freshness 透传 run 级主表面)。"""
    try:
        from contextos.code_intel.projection import store as proj_store
        engine = app_ctx.engine
        build_id = proj_store.get_meta(engine, "projection_build_id")
        if not build_id:
            return {"status": "not_built", "hint": "run `contextos init`"}
        out: dict[str, Any] = {
            "status": proj_store.get_meta(engine, "build_status") or "ok",
            "build_id": build_id,
            "indexed_commit": proj_store.get_meta(engine, "last_indexed_commit") or ""}
        try:    # 落后几个 commit(git 不可用就略过, 不冒泡)
            from contextos.code_intel.projection.paths import repo_root as _rr
            from contextos.util.subproc_text import decode_content, run_git
            repo = str(_rr(app_ctx.profile))
            behind = run_git(
                ["-C", repo, "rev-list", "--count", f"{out['indexed_commit']}..HEAD"],
                timeout=10)
            if behind.returncode == 0:
                out["commits_behind"] = int(decode_content(behind.stdout).strip() or 0)
        except Exception:
            pass
        return out
    except Exception as exc:
        return {"status": f"error: {exc}"}


# --------------------------------------------------------------------------- profile_info


def profile_info_impl(app_ctx: Any) -> dict[str, Any]:
    """profile 非敏感元信息(白名单输出, 绝不回显凭据;红线 #9)。

    返回 {profile_path, data_dir, repo_root, source_roots, oracle_instances,
    rag_corpora, missing_required, dispatch_patterns, carrier_read_patterns}。
    每个字段独立兜底:取不到的字段降级为安全空值/占位,
    不让单个缺字段把整个工具拖崩。
    """
    profile = getattr(app_ctx, "profile", None)
    return {
        "profile_path": _profile_path(),
        "data_dir": _data_dir(profile),
        "repo_root": _repo_root(profile),
        "source_roots": _source_roots(profile),
        "oracle_instances": _oracle_instances(profile),
        "rag_corpora": _rag_corpora(profile),
        "missing_required": _missing_required(profile),
        "dispatch_patterns": _dispatch_patterns(profile),
        "carrier_read_patterns": _carrier_read_patterns(profile),
    }


def _profile_path() -> str:
    """profile 来源指针。Profile(pydantic, extra=forbid)不存源路径, 故取 $CONTEXTOS_PROFILE
    环境指针(loader 的权威入口);未设则 '<not set>'。是路径非凭据, 可列。"""
    return os.environ.get("CONTEXTOS_PROFILE") or "<not set>"


def _data_dir(profile: Any) -> str:
    try:
        return str(Path(profile.storage.data_dir).expanduser())
    except Exception:
        return ""


def _repo_root(profile: Any) -> str:
    """客户码主仓根(绝对路径, 非凭据, 同 data_dir 类; 红线 #9)。E3 rg census 前置。"""
    try:
        from contextos.code_intel.projection.paths import repo_root
        return str(repo_root(profile))
    except Exception:
        return ""


def _source_roots(profile: Any) -> list[str]:
    """客户码扫描根(绝对路径列表; 空 source_roots -> [repo_root])。E3 rg census 跑在这里面。"""
    try:
        from contextos.code_intel.projection.paths import resolve_source_roots
        return [str(p) for p in resolve_source_roots(profile)]
    except Exception:
        return []


def _oracle_instances(profile: Any) -> list[str]:
    """白名单 TNS 实例**名**(非凭据;凭据走 env ORACLE_<TNS>_USER/_PASSWORD, 不在 profile)。"""
    try:
        return list(profile.oracle.allowed_instances)
    except Exception:
        return []


def _rag_corpora(profile: Any) -> list[str]:
    """已注册 corpus 子集**名**(03 §2.1)= profile.config.corpus_subset_prefixes 的键。

    只列名字, 不把 prefix 路径细节当值带出(子集名是注册枚举, 非敏感)。
    """
    try:
        return sorted(profile.config.corpus_subset_prefixes.keys())
    except Exception:
        return []


def _dispatch_patterns(profile: Any) -> list[str]:
    """框架字符串派发模式(非敏感: 框架类名/前缀, 非值/凭据; search_source caller census 用)。"""
    try:
        return list(profile.code.dispatch_patterns)
    except Exception:
        return []


def _carrier_read_patterns(profile: Any) -> list[str]:
    """配置载体读取模式(非敏感; search_source 消费方 census 用)。"""
    try:
        return list(profile.code.carrier_read_patterns)
    except Exception:
        return []


def _missing_required(profile: Any) -> list[str]:
    """缺失/违规的必填项清单(跨命名空间校验, 不做路径存在性检查 -> 离线/无盘也能查)。

    pydantic 已在 load 时强制结构必填字段;这里补跨命名空间约束(validate_profile),
    把它抛的合并错误拆成 list。任何异常(含 profile=None)降级为空 list, 不冒泡。
    脱敏:validate_profile 的错误文本只含字段名 / 实例名 / 路径, 不含凭据值。
    """
    if profile is None:
        return []
    try:
        from contextos.profile.validator import (
            ProfileValidationError,
            validate_profile,
        )

        try:
            validate_profile(profile, check_paths=False)
        except ProfileValidationError as exc:
            return [part.strip() for part in str(exc).split(";") if part.strip()]
        return []
    except Exception:
        return []


# --------------------------------------------------------------------------- incremental_rebuild


def incremental_rebuild_impl(app_ctx: Any, *, scope: str = "all") -> dict[str, Any]:
    """code scope 实装(Plan 04b T14): rebuild_entry 增量 + flock 单飞 + 撞阈值全量回退。

    scope="all" 当前等价 code(其余维度增量 v1.x 仍占位 -> not_implemented + 回显 scope)。
    并发安全: incremental_rebuild_code 内部 try_lock(projection_lockfile)非阻塞抢锁,
    别人持有 -> {"status": "already_running"}(spec §8 不排队阻塞)。
    """
    if scope not in ("all", "code"):
        return {"status": "not_implemented", "scope": scope}
    from contextos.code_intel.projection.rebuild_entry import incremental_rebuild_code
    res = incremental_rebuild_code(app_ctx.profile, app_ctx.engine,
                                   lockfile=app_ctx.projection_lockfile)
    return {"scope": "code", **res}


# --------------------------------------------------------------------------- MCP 注册


def register_meta_tools(mcp: FastMCP, app_ctx: Any) -> None:
    """把 3 元工具注册成 MCP tool(闭包捕获 app_ctx)。异常转 ToolError(红线 #9)。"""

    @mcp.tool()
    def health_check() -> dict[str, Any]:
        """体检:代码查询器(jdt_ls)/ Oracle / 模型 / engine / code 投影 各子系统状态(任一半 down 不影响整表返回)。"""
        try:
            return health_check_impl(app_ctx)
        except Exception as exc:  # impl 已逐项兜底, 这里只兜极端意外
            raise ToolError(f"health_check failed: {exc}") from exc

    @mcp.tool()
    def profile_info() -> dict[str, Any]:
        """当前 profile 非敏感元信息(实例名 / corpus 名 / 路径 / 缺失必填项)。绝不回显凭据。"""
        try:
            return profile_info_impl(app_ctx)
        except Exception as exc:
            raise ToolError(f"profile_info failed: {exc}") from exc

    @mcp.tool()
    def incremental_rebuild(scope: str = "all") -> dict[str, Any]:
        """code 投影增量重建(flock 单飞, 撞阈值同锁内全量回退);其余 scope 占位 not_implemented。"""
        try:
            return incremental_rebuild_impl(app_ctx, scope=scope)
        except Exception as exc:
            raise ToolError(f"incremental_rebuild failed: {exc}") from exc
