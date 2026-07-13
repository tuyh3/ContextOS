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
   - code_projection(Plan 04b T14)读 code_projection_meta:未 build -> not_built + hint
     (含 fresh 环境表都不存在的情形, 经 inspector has_table 判定, 不裸出 OperationalError;
     真库损坏仍直出 error 不吞);已 build -> {status, build_id, indexed_commit
     [, commits_behind]}(spec §9 freshness)。

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
        "mysql": _probe_mysql(app_ctx),
        "models": _probe_models(app_ctx),
        "engine": _probe_engine(app_ctx),
        "code_projection": _probe_code_projection(app_ctx),
        "ripgrep": _probe_ripgrep(app_ctx),
        "jdtls_runtime": _probe_jdtls_runtime(app_ctx),
    }


def _probe_jdtls_runtime(app_ctx: Any) -> dict[str, Any]:
    """展示生效来源(spec A6): 判定段改为调 resolver(resolve_effective_runtime),
    与 init/rebuild 实际取路径同一把尺, 防两套定义漂移。

    三态:
    - trio_source == "profile": profile 三路径深校验有效 -> {"status":"ok",
      "source":"profile"}(仍单独查 indexer_source, C1 反遮蔽见 _maybe_suggest_indexer_jar)。
    - trio_source == "runtime-bundle": profile 未过深校验但 cwd 下 runtime bundle
      四件套齐全 -> {"status":"ok","source":"runtime-bundle (fallback)",
      suggestion 四行(值=bundle 路径), hint 讲清"当前用包内运行时, 想钉死路径可
      照抄 suggestion 进 profile"}。
    - trio_source == "profile-unverified": 两边都没有(即 resolver 已判定 bundle
      缺席), 维持原 missing 探测支路(spec C1 顺序: bundle 建议 -> VSCode 扩展建议
      -> 安装指引), missing 清单仍用 _deep_validate_profile_runtime(委托版)取,
      行为与改造前一致。

    只探不写(回写 human-gated); 返回 dict 非 str, 因为 suggestion 是结构化内容。"""
    try:
        from contextos.code_intel.jdtls_provider.discovery import (
            resolve_effective_runtime,
        )
        rt = resolve_effective_runtime(app_ctx.profile, root=Path.cwd())
        if rt.trio_source == "profile":
            out: dict[str, Any] = {"status": "ok", "source": "profile"}
            _maybe_suggest_indexer_jar(rt, out)
            return out
        if rt.trio_source == "runtime-bundle":
            return {
                "status": "ok",
                "source": "runtime-bundle (fallback)",
                "suggestion": {
                    "jdtls_path": rt.jdtls_path,
                    "lombok_path": rt.lombok_path,
                    "java_home": rt.java_home,
                    "indexer_jar": rt.indexer_jar,
                    "source": "runtime-bundle",
                },
                "hint": (
                    "当前自动使用包内运行时; 想钉死路径可把 suggestion 照抄进 profile "
                    "[jdtls_runtime](三路径)+ [code_index].indexer_jar(TOML 里 Windows "
                    "路径用正斜杠)"
                ),
            }
        # trio_source == "profile-unverified": profile 未过深校验且 bundle 也探不到,
        # 维持原 missing 探测支路(spec C1 顺序, 此支路本身即为 bundle 缺席态), 与
        # 改造前行为一致。
        r = app_ctx.profile.jdtls_runtime
        missing = _deep_validate_profile_runtime(r)
        from contextos.code_intel.jdtls_provider.discovery import (
            discover_runtime_bundle,
            discover_vscode_jdtls,
        )
        out = {"status": "missing", "missing": missing}
        # 注意: 走到这里 resolver 已经用同一把尺(root=Path.cwd())判过 bundle 不在
        # (否则 trio_source 会是 "runtime-bundle" 而不会进这个 elif 分支), 所以下面
        # 这次 discover_runtime_bundle() 重探构造性恒返回 None —— 不是活路径, 是
        # unverified 态已蕴含的同锚探测结果, 留作逐字保留 + TOCTOU(检查和使用之间状态
        # 变化)兜底, 别误读成"这里还有机会探到 bundle"。
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
    """委托 discovery.validate_profile_runtime_paths(resolver/health 同一把尺,
    spec A11)。那边把浅 exists + 深校验合并成一段, 这里不再重复判定逻辑。"""
    from contextos.code_intel.jdtls_provider.discovery import (
        validate_profile_runtime_paths,
    )
    return validate_profile_runtime_paths(r)


def _maybe_suggest_indexer_jar(rt: Any, out: dict[str, Any]) -> None:
    """spec C1 反遮蔽(Task 7 改: 消费 resolver 算好的 indexer_source, 不再自己
    重复解析) —— 默认值指 vendor gitignored 路径, 用 VSCode 扩展的用户没有 bundle
    suggestion 入口, 这行补充建议是他们唯一能收到现成 indexer 的地方。best-effort:
    任何异常吞掉, 绝不把 ok 拖成 error(补充建议非判定)。

    rt: resolve_effective_runtime() 的返回值(EffectiveRuntime), 由调用方
    (_probe_jdtls_runtime 的 profile 分支)算好后传入 —— 出处(indexer_source)而非
    存在性判定"要不要建议", 避免与 trio 判定分开重复解析走漂(spec A11 同一把尺)。
    rt.indexer_source == "runtime-bundle" 时才建议(即"配置的原始 jar 缺、bundle
    兜底生效"这一态); "profile"(配置值本身生效)或 "profile-unverified"(两边都
    没有, 无从建议)均不附加。"""
    try:
        if rt.indexer_source == "runtime-bundle":
            out["indexer_jar_suggestion"] = rt.indexer_jar
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
    非 oracle 目标库(spec A.4 并列键)-> 'not_applicable', 不误报 offline。

    Block 1b: 使用 DbRouter.fan_out() 探活(多库支持);router 内部降级不崩(nil-safe)。
    """
    try:
        if getattr(app_ctx.profile.database, "type", None) != "oracle":
            return "not_applicable"
    except Exception:
        pass
    try:
        router = app_ctx.oracle_router()
        connected = bool(router and router.fan_out())
        return "connected" if connected else "offline"
    except Exception:
        # router 本应自降级;任何异常一律视作离线, 不把 health_check 拖崩。
        return "offline"


def _probe_mysql(app_ctx: Any) -> str:
    """mysql profile -> 急连探活 connected/offline; 非 mysql 类型 -> not_applicable(spec A.4)。

    连接经 connect_mysql_from_profile(白名单闸 + 凭据 env), __enter__ 急连探活;
    任何异常(缺凭据/库不通/闸拒)一律 offline, 不把 health 拖崩。"""
    try:
        if getattr(app_ctx.profile.database, "type", None) != "mysql":
            return "not_applicable"
    except Exception:
        return "not_applicable"
    try:
        from contextos.db_provider.mysql_client import connect_mysql_from_profile
        with connect_mysql_from_profile(app_ctx.profile):
            return "connected"
    except Exception:
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
        from sqlalchemy import inspect as sa_inspect

        from contextos.code_intel.projection import schema as proj_schema
        from contextos.code_intel.projection import store as proj_store
        engine = app_ctx.engine
        # fresh 环境(init 前)meta 表不存在, 语义 = 尚未构建, 不把裸 OperationalError
        # 直出吓用户;只认"表不存在"这一种缺席 —— 真损坏(inspect 自己会抛)仍走外层
        # except 直出 error, 绝不吞成 not_built。has_table 走 SQLAlchemy inspector,
        # sqlite/信创 PG 通用, 不做 "no such table" 这类方言字符串匹配。
        if not sa_inspect(engine).has_table(proj_schema.code_projection_meta.name):
            return {"status": "not_built", "hint": "run `contextos init`"}
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
        "mysql_instances": _mysql_instances(profile),
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
        # 统一取值点 profile.database; 非 oracle 类型(database.oracle=None)走 except 返空
        return list(profile.database.oracle.allowed_instances)
    except Exception:
        return []


def _mysql_instances(profile: Any) -> list[str]:
    """白名单 MySQL 实例别名(非凭据;凭据走 env MYSQL_<ALIAS>_USER/_PASSWORD, 不在 profile)。"""
    try:
        return [i.alias for i in profile.database.mysql.instances]
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
