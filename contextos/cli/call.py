"""contextos call: 单独调用任一 MCP tool(不需要 AI editor / MCP Inspector 的 ad-hoc 测试入口)。

薄适配: 配 profile -> AppContext.from_profile -> build_server -> in-memory fastmcp Client ->
call_tool(name, args) -> 打印结果 JSON。与 query 命令共用同一条 AppContext/build_server 装配
路径(main.py 的 serve-mcp / query 已验证过的路子),本层只加"只跑一个 tool"的薄壳。

参数来源二选一(不并存优先级见 call() docstring):
  --args '<json>'        内联 JSON object 字符串。
  --args-file <path>     从 UTF-8 文件读 JSON object(Windows 友好: 绕开 cmd/PowerShell
                         的引号转义地狱 —— 双引号在 PowerShell 里要么被吃掉要么要三层转义,
                         文件路径没有这个问题)。

退出码:
  0   成功, 结果 JSON 打到 stdout。
  1   tool 执行期错误(fastmcp ToolError / middleware 拒绝) —— 错误消息打到 stderr。
  2   用户输入错误(JSON 解析失败 / 非 object / 未知 tool 名) —— 错误消息打到 stderr,
      未知 tool 名额外把可用 tool 列表打出来。

跨平台: 只用 asyncio.run 包 async 部分, 无 POSIX-only API(无 fork/fcntl/signal/shell=True)。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import typer

from contextos.mcp_server.app_context import AppContext
from contextos.mcp_server.server import build_server
from contextos.profile.loader import load_profile


class _UserInputError(Exception):
    """--args / --args-file 解析失败, 或未知 tool 名(exit code 2 的统一载体)。"""


class _ToolExecutionError(Exception):
    """tool body 执行期错误(fastmcp ToolError / middleware 拒绝, exit code 1 的载体)。"""


def _load_args(args: str | None, args_file: str | None) -> dict[str, Any]:
    """解析 tool 入参 JSON object。--args-file 优先于 --args(若两者都传)。"""
    if args_file is not None:
        path = Path(args_file)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise _UserInputError(f"读取 --args-file 失败: {path}: {exc}") from exc
    elif args is not None:
        raw = args
    else:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _UserInputError(f"参数不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise _UserInputError(
            f"参数必须是 JSON object(如 {{\"key\": \"value\"}}), 实际是 {type(parsed).__name__}"
        )
    return parsed


def _format_schema_human(description: str | None, input_schema: dict[str, Any]) -> str:
    """把 inputSchema(JSON Schema object)转成人读参数列表 + 附原始 JSON(可 jq)。

    每个 property 一行: 名字 / type / required-or-default。required 由顶层 "required"
    数组决定(与 JSON Schema 语义一致, 不是每个 property 自带的字段)。
    """
    lines: list[str] = []
    if description:
        lines.append(description)
        lines.append("")
    props: dict[str, Any] = input_schema.get("properties", {}) or {}
    required = set(input_schema.get("required", []) or [])
    if not props:
        lines.append("(无参数)")
    else:
        lines.append("参数:")
        for name, spec in props.items():
            spec = spec if isinstance(spec, dict) else {}
            ptype = spec.get("type", "any")
            if name in required:
                req_desc = "必填"
            elif "default" in spec:
                req_desc = f"可选, 默认 {spec['default']!r}"
            else:
                req_desc = "可选"
            lines.append(f"  - {name}: {ptype}, {req_desc}")
    lines.append("")
    lines.append("原始 inputSchema(JSON):")
    lines.append(json.dumps(input_schema, ensure_ascii=False, indent=2))
    return "\n".join(lines)


async def _describe_tool_async(app_ctx: AppContext, tool_name: str) -> str:
    """查 tool 的描述 + 参数 schema, 不执行(不调 call_tool)。未知 tool 复用同一条
    exit-2 错误路径(_UserInputError, 与 _call_tool_async 的未知 tool 分支同文案)。"""
    from fastmcp import Client

    server = build_server(app_ctx)
    async with Client(server) as client:
        tools = await client.list_tools()
        by_name = {t.name: t for t in tools}
        if tool_name not in by_name:
            available = ", ".join(sorted(by_name))
            raise _UserInputError(f"未知 tool: {tool_name!r}. 可用 tool: {available}")
        tool = by_name[tool_name]

    header = f"Tool: {tool.name}"
    body = _format_schema_human(tool.description, tool.inputSchema or {})
    return f"{header}\n\n{body}"


async def _call_tool_async(app_ctx: AppContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    """起 in-memory server + Client, 调一个 tool, 返回其结构化结果。

    结果提取: 优先 res.structured_content(MCP host 在 wire 上真收到的 payload), 对
    FastMCP 用 x-fastmcp-wrap-result 标记包过的非 object 返回型(如 `list[dict[str, Any]]`,
    元素 Any 建不出精确 schema)按该标记解 {"result": [...]} 包装 —— 与
    mcp_server/smoke/smoke_real.py._call 的既有定式一致(那里记录了实测坑: res.data 会把
    这类元素反序列化成空壳 Root() 丢字段, 见该文件 _call docstring)。
    structured_content 为 None(理论上不会, 当前所有 tool 都有输出 schema)时退回 res.data 兜底。
    """
    from fastmcp import Client
    from fastmcp.exceptions import ToolError as FastMCPToolError

    server = build_server(app_ctx)
    async with Client(server) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        if tool_name not in names:
            available = ", ".join(sorted(names))
            raise _UserInputError(f"未知 tool: {tool_name!r}. 可用 tool: {available}")

        try:
            res = await client.call_tool(tool_name, tool_args)
        except FastMCPToolError as exc:
            raise _ToolExecutionError(str(exc)) from exc

        sc = res.structured_content
        if sc is None:
            return res.data
        out_schema = next((t.outputSchema for t in tools if t.name == tool_name), None) or {}
        if out_schema.get("x-fastmcp-wrap-result") and isinstance(sc, dict):
            return sc.get("result")
        return sc


def call(
    tool_name: Annotated[str, typer.Argument(help="要调用的 MCP tool 名(如 profile_info / lookup_table)。")],
    args: Annotated[
        str | None,
        typer.Option("--args", help="内联 JSON object 字符串, 如 '{\"table\": \"CB_CUSTOMER\"}'。省略则用 {}。"),
    ] = None,
    args_file: Annotated[
        str | None,
        typer.Option(
            "--args-file",
            help="从 UTF-8 文件读 JSON object(Windows 友好, 绕开 shell 引号转义)。"
            "若同时传 --args, 以 --args-file 为准。",
        ),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", help="profile.toml 路径")] = None,
    describe: Annotated[
        bool,
        typer.Option(
            "--describe",
            help="不执行, 只打印该 tool 的描述 + 参数 schema(不知道传什么参数时先看这个)。"
            "与 --args/--args-file 同传时 --describe 优先, 后两者被忽略。",
        ),
    ] = False,
) -> None:
    """单独调用任一 MCP tool(不起常驻 server, 不需要 AI editor / MCP Inspector)。

    结果 JSON 打到 stdout(ensure_ascii=False, indent=2, 与 query 命令同一形态)。
    """
    profile_obj = load_profile(Path(profile) if profile else None)
    app_ctx = AppContext.from_profile(profile_obj)

    if describe:
        try:
            text = asyncio.run(_describe_tool_async(app_ctx, tool_name))
        except _UserInputError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        typer.echo(text)
        return

    try:
        tool_args = _load_args(args, args_file)
    except _UserInputError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        result = asyncio.run(_call_tool_async(app_ctx, tool_name, tool_args))
    except _UserInputError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except _ToolExecutionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def register(app: typer.Typer) -> None:
    """把 call 命令注册进共享 app(main.py 调; 与 init.register / suggest_stop_keywords.register 同一模式)。"""
    app.command("call")(call)
