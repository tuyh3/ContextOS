"""contextos CLI(Typer)—— Plan 10 §4 对外三形态之一。

定位:MCP server / Python lib 之外的第三种入口,给 shell / CI / 人手交互用。八命令:

  serve-mcp   起 FastMCP stdio server(给 Claude Code / Cursor 经 .mcp.json 接入)。
  query       一次性跑 build_impact_map,把 Impact Map JSON 打到 stdout(给 CI / 管道
              ad-hoc 查询,不需要常驻 server)。
  rebuild     增量重建 code_* 投影(04b spec §5.3 CLI 入口,与 MCP/watcher 同一把锁)。
  health      探活体检 + profile 非敏感元信息组合 JSON(给 SessionStart hook /
              skill Phase 0 自跑 / shell / CI;组件 C 横切探活基建)。
  run-evaluation  [v1.x deferred -> Plan 09] 占位,评测 runner 还没接线。
  init        初始化客户四证据维度(实现在 cli/init.py,经文件尾 register_init 挂进
              同一 app,避免反向 import 循环)。
  suggest-stop-keywords  扫源码生成停用词草稿(spec 附录 D7;实现在
              cli/suggest_stop_keywords.py,同 register(app) 模式挂进同一 app)。
  call        单独调用任一 MCP tool(不需要 AI editor / MCP Inspector 的 ad-hoc 测试
              入口;实现在 cli/call.py,同 register(app) 模式挂进同一 app)。

接线原则(与 MCP server 共用同一套下游):各命令都先 load_profile(profile) 再调下游 ——
  serve-mcp / query 经 AppContext.from_profile 共享重资源(build_server 起服务 /
  build_impact_map_impl 跑一次);rebuild 不起 AppContext 全家桶,直连 engine_from_profile
  (一次性命令只需 engine + 投影锁);init 走 init.orchestrator.run_init 编排。CLI 本身
  **只做参数解析 + 调下游 + 输出**,不写任何编排 / 检索逻辑(那些在 08 orchestrator +
  mcp_server.tools)。重资源(JDT / Oracle / RAG)由 AppContext lazy 持有,query 这种
  一次性命令进程结束即随之释放。

`app` 变量是 [project.scripts] 的入口(`contextos = "contextos.cli.main:app"`),不要改名。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from contextos.code_intel.projection.rebuild_entry import incremental_rebuild_code
from contextos.mcp_server.app_context import AppContext
from contextos.mcp_server.server import build_server
from contextos.mcp_server.tools.impact_map import build_impact_map_impl
from contextos.mcp_server.tools.meta import health_check_impl, profile_info_impl
from contextos.profile.loader import load_profile
from contextos.storage.db import engine_from_profile

log = logging.getLogger(__name__)


def _resolve_profile_arg(profile: str | None) -> Path | None:
    """把 CLI 的 --profile 字符串归一为 load_profile 期望的 Path | None。

    None(未传)透传给 load_profile,让它走搜索优先级(env / cwd / home);
    传了路径则包成 Path(load_profile 内部再 expanduser/resolve)。
    """
    return Path(profile) if profile is not None else None

app = typer.Typer(
    add_completion=False,
    help="ContextOS 需求影响定位器 —— MCP server / 一次性查询 / 评测 入口。",
)


@app.callback()
def _load_env() -> None:
    """所有子命令前置: 加载仓根 .env(DB 凭据 MYSQL_<ALIAS>_USER/_PASSWORD / API key 等)。

    mysql_client 等按"凭据由上游 CLI 已加载"设计(不自己 load_dotenv, 避免每次连接重读文件);
    此前只有 llm.factory / sqlcl_mcp 各自 load_dotenv, `init --only database`(不走 LLM)时
    MySQL 凭据无人加载 -> DbSafetyError(实验实测)。在 app 级统一加载, 兜住全命令。
    load_dotenv 不覆盖已 export 的同名变量(export 优先)。"""
    from dotenv import load_dotenv
    load_dotenv()

_ProfileOpt = Annotated[
    str | None,
    typer.Option(
        "--profile",
        help="profile.toml 路径;省略则按 load_profile 搜索优先级"
        "($CONTEXTOS_PROFILE -> ./profile.toml -> ./config/profile.toml"
        " -> ./data/profile.toml -> ~/.config/contextos/ -> ~/contextos-fpa/)。",
    ),
]


@app.command("serve-mcp")
def serve_mcp(
    stdio: Annotated[
        bool,
        typer.Option(
            "--stdio/--no-stdio",
            help="用 stdio transport(默认;Claude Code / Cursor 走这个)。",
        ),
    ] = True,
    profile: _ProfileOpt = None,
) -> None:
    """起 FastMCP server(stdio),对外暴露 build_impact_map + 15 证据 tool + 3 元工具(共 19)。

    阻塞运行直到 host 断开(stdio 长连接)。HTTP transport 是 v2 升级路径,v1 只 stdio。
    """
    profile_obj = load_profile(_resolve_profile_arg(profile))
    app_ctx = AppContext.from_profile(profile_obj)
    # 04b T14: JDT 预热已删 —— 代码查询走 code_* 持久投影(ProjectionSearcher 查表秒回,
    # 零 JDT 冷启), JDT 只在 init/增量 build 期。
    # 04b T15: watcher + 启动补课(spec §5.3)。watcher/补课起不来不能挡 server 启动
    # (投影查询本身不依赖 watcher, 只是少了运行期自动增量), 故整段 try/except 兜底。
    try:
        from contextos.code_intel.projection.watcher import start_projection_watch
        start_projection_watch(app_ctx)
    except Exception as exc:  # noqa: BLE001
        log.warning("projection watcher wiring failed (server continues): %s", exc)
    mcp = build_server(app_ctx)
    # stdio 入参保 API 稳定;v1 只支持 stdio transport(HTTP 是 v2,见红线 #8)。
    _ = stdio
    mcp.run(transport="stdio")


@app.command("query")
def query(
    requirement: Annotated[
        str,
        typer.Argument(help="需求文本, 或文件路径(配 --adapter-kind docx/email)。"),
    ],
    adapter_kind: Annotated[
        str,
        typer.Option(
            "--adapter-kind",
            help="输入类型 text/docx/email(docx/email 时 requirement 传文件路径)。"
            "不自动探测后缀, 用户显式声明(安全红线 #9)。",
        ),
    ] = "text",
    profile: _ProfileOpt = None,
) -> None:
    """一次性跑 build_impact_map,把 Impact Map JSON 打到 stdout(给 shell / CI ad-hoc)。

    输出 = 01 schema 的完整 JSON(version / evidence_items / dimensions / ...),
    ensure_ascii=False 保中文可读,indent=2 便于人眼 / jq 消费。
    """
    profile_obj = load_profile(_resolve_profile_arg(profile))
    app_ctx = AppContext.from_profile(profile_obj)
    try:
        result = build_impact_map_impl(app_ctx, requirement=requirement, adapter_kind=adapter_kind)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("rebuild")
def rebuild(
    scope: Annotated[
        str,
        typer.Option("--scope", help="重建维度;v1 唯一实装 = code(code_* 投影增量)。"),
    ] = "code",
    profile: _ProfileOpt = None,
) -> None:
    """增量重建 code_* 投影(撞阈值 / 指纹变更 / 无基准自动转全量;spec §5.3 CLI 入口)。

    内部走 rebuild_entry.incremental_rebuild_code —— 与 MCP incremental_rebuild /
    watcher 同一持锁入口(data_dir/projection.lock, 同 app_context.projection_lockfile
    口径)。退出码: ok/noop=0, already_running/degraded 等=1。
    """
    if scope != "code":
        typer.echo(json.dumps({"status": "not_implemented", "scope": scope},
                              ensure_ascii=False))
        raise typer.Exit(code=1)
    profile_obj = load_profile(_resolve_profile_arg(profile))
    engine = engine_from_profile(profile_obj)
    lockfile = Path(profile_obj.storage.data_dir).expanduser() / "projection.lock"
    res = incremental_rebuild_code(profile_obj, engine, lockfile=lockfile)
    typer.echo(json.dumps(res, ensure_ascii=False, indent=2))
    if res.get("status") not in ("ok", "noop"):
        raise typer.Exit(code=1)


@app.command("run-evaluation")
def run_evaluation() -> None:
    """[v1.x deferred -> Plan 09] 评测 runner 占位。

    09 评测与样本管理的 runner 尚未接线;此命令先占位,避免入口缺命令报错,
    并给出明确指针。真实现见 Plan 09(eval/ 模块)。
    """
    typer.echo("evaluation runner not yet wired (Plan 09)")


@app.command("health")
def health(
    profile: _ProfileOpt = None,
) -> None:
    """探活体检 + profile 非敏感元信息, 组合成一份 JSON 打到 stdout(给 SessionStart hook /
    shell / CI / skill Phase 0 自跑)。

    输出 = {"health": {...}, "profile_info": {...}}:
      health      = health_check_impl(app_ctx): jdt_ls / oracle / models / engine /
                    code_projection / ripgrep / jdtls_runtime 各子系统状态(任一半 down
                    不影响整表; jdtls_runtime 缺路径时附 VSCode 扩展自动探测建议)。
      profile_info= profile_info_impl(app_ctx): 白名单非敏感元信息(实例名 / corpus 名 /
                    路径 / 缺失必填项), 绝不回显凭据(红线 #9)。

    两个 impl 各自逐项 try/except 兜底, 故 health 命令在任意半 down 态下都能产出整表,
    exit_code 0(探活本身不该因子系统 down 而失败 —— 失败语义留给 hook 层的 fail-open)。
    ensure_ascii=False 保中文路径可读, indent=2 便于人眼 / jq 消费。
    """
    profile_obj = load_profile(_resolve_profile_arg(profile))
    app_ctx = AppContext.from_profile(profile_obj)
    out = {
        "health": health_check_impl(app_ctx),
        "profile_info": profile_info_impl(app_ctx),
    }
    typer.echo(json.dumps(out, ensure_ascii=False, indent=2))


from contextos.cli.call import register as register_call  # noqa: E402
from contextos.cli.init import register as register_init  # noqa: E402
from contextos.cli.suggest_stop_keywords import register as register_suggest_stop_keywords  # noqa: E402

register_init(app)
register_suggest_stop_keywords(app)
register_call(app)

if __name__ == "__main__":  # pragma: no cover
    app()
