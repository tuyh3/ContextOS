"""InputValidationMiddleware — host 不可信的输入硬隔离(Plan 10 Task 9,红线 #8/#9)。

定位
----
FastMCP middleware,挂在 build_server 返回的 server 上(mcp.add_middleware)。它在
**所有** tool call 进入 tool body 之前做一道**粗粒度**输入隔离 —— MCP host(Claude
Code / Cursor / 任意接入方)不可信(红线 #9),不能靠它自觉传安全参数。这是 design §5
的「第一层」(统一拦截),与 evidence.py tool body 的脱敏 + core 的 identifier 校验
(第二层 profile 枚举细校验)互补:第一层挡掉结构性危险(注册外语料 / 注入连接 / 非只读
SQL / 非 FQN 形态),第二层管字段级 profile 枚举一致性。

放这一层(而非散在 19 个 tool body)的理由:corpora 白名单 / 连接键黑名单 / 只读 SQL
是跨 tool 的统一约束,集中在 on_call_tool 一处拦截,比每个 tool body 重复写干净,且新增
tool 自动受同一道闸门保护(FastMCP middleware.md 推荐用法)。

四类硬拒(命中任一 -> raise ToolError -> MCP isError -> host 收到错误,tool body 不执行)
----------------------------------------------------------------------------------
1. ad-hoc corpus(红线 #9 host 不可信):凡 arguments 带 `corpora`,每个值必须 ∈ profile
   注册的 corpus 子集枚举(profile.config.corpus_subset_prefixes 的键,03 §2.1)。host
   不能塞一个临时语料名/路径让 RAG 去搜未授权目录(防 host 越权访问未注册语料)。
   全集约束:列表里**任一**值未注册即整调用拒(不是任一命中即放行)。
2. 连接注入(红线 #4 Oracle 白名单 / #9 host 不可信):arguments 含 tns/dsn/connection/
   db_url/conn 等连接键即拒。Oracle 连接由 profile + env(ORACLE_<TNS>_USER/_PASSWORD)
   定,host 绝不能注入连接串绕过 allowed_instances 白名单去连生产库。注:FastMCP 自身
   pydantic 校验也会把未知 kwarg 当 unexpected_keyword_argument 拒,但那是"参数不认识"
   的偶然兜底、错误信息含糊;本层是**显式安全拒绝**(清晰 message + 防某连接键恰好撞上某
   tool 真参数名时漏网),是有意的纵深防御。
3. 非只读 SQL(红线 #4):带 SQL 形态参数(`sql` 键,或 `pattern` 里被识别为完整 SQL 语句
   的串)的,过 oracle_gate.assert_query_is_readonly —— 非 SELECT/WITH、多语句、12
   forbidden keyword 即拒。**关键边界**:search_sql 的 `pattern` 主用法是字面 grep 片段
   (如 "FROM ORDERS"),不能把每个 grep 片段都当 SQL 拒(否则 search_sql 全废);故只对
   "看起来像一条完整 SQL 语句"的 pattern 套闸门(见 _looks_like_sql),纯字面片段放行。
4. FQN 形态(Plan 04b T14,红线 #9):read_symbol/lookup_calls 的 fqn/method_fqn 必须像
   Java FQN(_FQN_RE)且 <=512 字符 —— 拒路径穿越(../)/ shell 元字符 / 超长输入。
   投影 tool 只收 FQN 不收路径(路径由投影表内部解析,spec §7 护栏 1 的前置闸)。

错误语义
--------
拦截一律 raise fastmcp.exceptions.ToolError(被 FastMCP 转成 MCP isError 回给 host),
不裸抛 traceback / 不泄漏内部栈(红线 #9)。message 只含"哪类违规 + 触发值的安全摘要"
(corpus 名 / 连接键名 / 闸门拒绝原因),不回显凭据。oracle_gate 抛的 OracleSafetyError
在此包成 ToolError(统一错误类型)。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext

from contextos.db_provider.oracle_gate import (
    OracleSafetyError,
    assert_query_is_readonly,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from contextos.mcp_server.app_context import AppContext

# host 不能注入的连接键(大小写不敏感匹配 arguments 的键名)。
_CONNECTION_KEYS = frozenset({"tns", "dsn", "connection", "db_url", "conn"})

# 4. FQN 形态校验(Plan 04b T14, 红线 #9): read_symbol/lookup_calls 只收 Java FQN,
# 不收路径(路径由投影表内部解析)。regex 容纳构造器 <init> 段;带签名的 method_fqn
# 括号段限长且拒 ;|& 元字符(防注入形态);路径穿越(../)/超长一律拒。
_FQN_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(\.[A-Za-z_$<][A-Za-z0-9_$>]*)*(\([^;|&]{0,512}\))?$")
_FQN_MAX = 512
_FQN_ARGS = ("fqn", "method_fqn")

# arguments 里被当作潜在 SQL 串审查的参数键(value 是字符串时才查)。
_SQL_PARAM_KEYS = frozenset({"sql", "pattern"})

# 任意 query 串长度上限(search_code / search_source 共用; 防超长 query 滥用成任意 grep / DoS)。
_QUERY_KEYS = frozenset({"query"})
_QUERY_MAX = 512

# "看起来像一条完整 SQL 语句"的前缀关键词:DML/DDL/DCL/PLSQL 动词。命中(或含语句分隔符 ;)
# 才把该参数当 SQL 过 readonly 闸门;纯字面 grep 片段(FROM .. / 列名)不命中 -> 放行。
# SELECT/WITH 也纳入:一条以 SELECT 开头的真 SQL 串确实该过只读闸门(它会通过),但若
# 是 "SELECT ... ; DROP ..." 多语句/含 forbidden 词则被拦,符合预期。
_SQL_STATEMENT_PREFIX = re.compile(
    r"^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|DROP|TRUNCATE|MERGE|CREATE|ALTER|"
    r"GRANT|REVOKE|EXEC|EXECUTE|CALL|BEGIN)\b",
    re.IGNORECASE,
)


def _looks_like_sql(value: str) -> bool:
    """判该字符串是否像一条完整 SQL 语句(而非字面 grep 片段)。

    命中条件(任一):以 SQL 语句动词开头,或含语句分隔符 `;`。后者覆盖把多语句藏在
    pattern 里的注入尝试(如 "x'; DROP TABLE t; --")。
    """
    if ";" in value:
        return True
    return bool(_SQL_STATEMENT_PREFIX.match(value))


class InputValidationMiddleware(Middleware):
    """tool call 前的粗粒度输入隔离(红线 #8/#9)。构造时捕获 app_ctx 以读 profile 白名单。"""

    def __init__(self, app_ctx: AppContext) -> None:
        self._app_ctx = app_ctx

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: Callable[[MiddlewareContext[Any]], Awaitable[Any]],
    ) -> Any:
        """所有 tool call 入口:校验 arguments,通过则放行 call_next,否则 raise ToolError。"""
        args = getattr(context.message, "arguments", None) or {}
        if isinstance(args, dict):
            self._check_connection_injection(args)
            self._check_corpora(args)
            self._check_readonly_sql(args)
            self._check_fqn_args(args)
            self._check_query_length(args)
        return await call_next(context)

    # ----------------------------------------------------------------- 1. ad-hoc corpus

    def _check_corpora(self, args: dict[str, Any]) -> None:
        """arguments['corpora'] 每个值必须是 profile 注册的 corpus 子集名(红线 #9 host 不可信)。"""
        if "corpora" not in args:
            return
        raw = args["corpora"]
        if raw is None:
            return
        requested = raw if isinstance(raw, (list, tuple, set)) else [raw]
        registered = self._registered_corpora()
        for name in requested:
            if name not in registered:
                # 消息里的 "(red line #2)" 是历史编号: 此守卫属敏感值/host 输入红线家族
                # (现 #9), 文案沿用旧编号不改(运行期字符串=行为面), 见根 CLAUDE.md
                # 约束 #2 歧义警告。
                raise ToolError(
                    f"corpus {name!r} not registered; host 不能注入 ad-hoc corpus"
                    " (red line #2). allowed: " + ", ".join(sorted(registered))
                )

    def _registered_corpora(self) -> set[str]:
        """profile.config.corpus_subset_prefixes 的键集(03 §2.1 已注册子集枚举)。

        取不到字段(profile 形态异常)-> 空集 = 拒一切 corpora(fail-safe:host 不可信,
        宁可全拒也不放行未知语料)。
        """
        try:
            prefixes = self._app_ctx.profile.config.corpus_subset_prefixes
            return set(prefixes.keys())
        except Exception:
            return set()

    # ----------------------------------------------------------------- 2. 连接注入

    def _check_connection_injection(self, args: dict[str, Any]) -> None:
        """arguments 含任何连接键 -> 拒(连接由 profile/env 定,红线 #4/#9)。"""
        for key in args:
            if isinstance(key, str) and key.lower() in _CONNECTION_KEYS:
                raise ToolError(
                    f"connection parameter {key!r} not allowed; host 不能注入数据库连接"
                    " (red line #4/#9). 连接由 profile + env 定。"
                )

    # ----------------------------------------------------------------- 4. FQN 形态(04b)

    def _check_fqn_args(self, args: dict[str, Any]) -> None:
        """fqn/method_fqn 参数必须像 Java FQN(红线 #9: 拒路径穿越/超长/注入形态)。"""
        for key in _FQN_ARGS:
            v = args.get(key)
            if v is None:
                continue
            if not isinstance(v, str) or len(v) > _FQN_MAX or not _FQN_RE.match(v):
                raise ToolError(f"invalid {key}: must be a Java FQN (max {_FQN_MAX} chars)")

    # ----------------------------------------------------------------- 5. query 长度上限(Task 5)

    def _check_query_length(self, args: dict[str, Any]) -> None:
        """query 参数长度上限(红线 #8/#9: 防超长 query 滥用 / DoS)。"""
        for key in _QUERY_KEYS:
            v = args.get(key)
            if isinstance(v, str) and len(v) > _QUERY_MAX:
                raise ToolError(f"invalid {key}: too long (max {_QUERY_MAX} chars)")

    # ----------------------------------------------------------------- 3. 非只读 SQL

    def _check_readonly_sql(self, args: dict[str, Any]) -> None:
        """SQL 形态参数过只读闸门(红线 #4);字面 grep 片段放行。"""
        for key in _SQL_PARAM_KEYS:
            value = args.get(key)
            if not isinstance(value, str):
                continue
            # sql 键:一律视作 SQL 串过闸门。pattern 键:仅当像完整 SQL 语句才过闸门。
            if key == "sql" or _looks_like_sql(value):
                try:
                    assert_query_is_readonly(value)
                except OracleSafetyError as exc:
                    raise ToolError(
                        f"non-readonly SQL in {key!r} refused (red line #4): {exc}"
                    ) from exc
