"""16 证据 tool 的 MCP 包装(Plan 10 Task 7 / Block 1b Task 13 / Plan 04b T14 / 覆盖基座 ① T7)。

register_evidence_tools(mcp, app_ctx) 把 05/06/04/03 维的 16 个 core 证据 tool 注册成
MCP tool(@mcp.tool())。本层是**薄包装**,严格只做四件事(spec §设计单元边界):
  1. 解析参数 -> 取 app_ctx 资源(engine / searcher / rag_provider / oracle_router())
  2. call 对应 core 函数(查询逻辑在 WF2 的 *tools.py / dataflow.py,各自单测,**不在此重写**)
  3. 异常转 fastmcp ToolError(不裸抛 traceback / 不泄漏内部栈给不可信 host,红线 #9)
  4. 返 dict / list(core 已返纯 JSON 友好结构)

16 个 tool:
  lineage(传 engine + router=app_ctx.oracle_router(),离线 None 走降级):
    lookup_table / lookup_lineage / lookup_dependency / lookup_sequence / search_sql
  lineage.dataflow(传 engine):trace_method_dataflow
  config(传 engine + patterns/salt,见下「敏感词来源」):
    lookup_config / lookup_rule / trace_config_impact / explain_rule_logic / diff_config
  code(04b 投影, 零 JDT):search_code(传 searcher=ProjectionSearcher)/
    lookup_calls(传 engine, caps 从 profile.code_index 取)/
    read_symbol(传 engine + profile 路径口径, FQN-only 由 middleware 先校验)/
    search_source(原始源码文本检索, 服务端 rg, 只搜 profile source_roots, 脱敏返回)
  rag(传 rag_provider):rag_search

敏感词来源(承接 WF2 安全修复,红线 #9 host 不可信)
------------------------------------------------
config 类 tool 的 `patterns` **从 profile.config.sensitive_key_patterns 取**(客户专属
敏感词,逐 profile 配),**不**让 host 传。core tools.py 内部还有 _DEFAULT_SENSITIVE_PATTERNS
floor 兜底(caller 传空也强制脱敏通用凭据 key);profile patterns 与 floor 取并集。
`salt` 由 load_or_create_salt(<data_dir>)取(本地 .config_salt,不入 Git);lookup_config
契约需 salt 入参(当前只为 fingerprint 对齐位,读取层不重算)。两者懒解析(首次调 config
tool 时按 app_ctx.profile 解析 + 在 data_dir 下落 salt 文件)。

corpora 白名单 / TNS 注入 / 只读 SQL / FQN 形态的粗粒度拦截是 middleware(Task 9 + 04b T14)的事,本层不重复
(只在 core 已有的 identifier 校验抛 ValueError 时转 ToolError)。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastmcp.exceptions import ToolError

from contextos.code_intel.code_search.tools import search_code_query
# tool 函数名 lookup_calls / read_symbol 不能撞 import 名, core 函数用别名引入。
from contextos.code_intel.projection.calls_query import lookup_calls as _lookup_calls
from contextos.code_intel.projection.method_resolve import AmbiguousMethodFqn
from contextos.code_intel.projection.paths import repo_root, resolve_source_roots
from contextos.code_intel.projection.source_slice import (
    SymbolNotFound,
    get_symbol_source,
)
from contextos.code_intel.source_search import (
    RipgrepUnavailable,
    search_source as _search_source,
)
from contextos.config_dim import tools as config_tools
from contextos.config_dim.sensitive import load_or_create_salt
from contextos.lineage import tools as lineage_tools
from contextos.lineage.dataflow import trace_method_dataflow as _trace_method_dataflow
from contextos.recall.rag_tool import rag_search as _rag_search

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from contextos.mcp_server.app_context import AppContext


def _sensitive_patterns(app_ctx: AppContext) -> list[str]:
    """config tool 的脱敏词:从 profile.config.sensitive_key_patterns 取(客户专属)。

    host 不可信(红线 #9):敏感词由 profile 定,绝不让 host 传。core 内部还有 floor
    兜底,profile patterns 与之取并集。取不到字段 -> 空 list(靠 core floor)。
    """
    cfg = getattr(app_ctx.profile, "config", None)
    pats = getattr(cfg, "sensitive_key_patterns", None) if cfg is not None else None
    return list(pats) if pats else []


def _config_salt(app_ctx: AppContext) -> bytes:
    """lookup_config 契约需 salt:从 load_or_create_salt(<data_dir>)取(本地,不入 Git)。"""
    data_dir = Path(app_ctx.profile.storage.data_dir).expanduser()
    return load_or_create_salt(data_dir)


def register_evidence_tools(mcp: FastMCP, app_ctx: AppContext) -> None:
    """把 16 个 core 证据 tool 注册成 MCP tool(闭包捕获 app_ctx)。

    每个 tool body:取资源 -> call core -> 异常转 ToolError -> 返结果。无查询逻辑。
    """

    # ------------------------------------------------------------------ lineage 维(5)

    @mcp.tool()
    def lookup_table(table: str, owner: str = "") -> dict[str, Any]:
        """表元数据:本地 lineage 边计数 + (Oracle 在线时)列/注释。离线返本地部分。"""
        try:
            return lineage_tools.lookup_table(
                app_ctx.engine, table=table, owner=owner,
                router=app_ctx.oracle_router())
        except Exception as exc:
            raise ToolError(f"lookup_table failed: {exc}") from exc

    @mcp.tool()
    def lookup_lineage(table: str, direction: str = "both") -> dict[str, Any]:
        """表上下游血缘(本地 lineage_edges + Oracle 依赖/同义词三路合并)。"""
        try:
            return lineage_tools.lookup_lineage(
                app_ctx.engine, table=table, direction=direction,
                router=app_ctx.oracle_router())
        except Exception as exc:
            raise ToolError(f"lookup_lineage failed: {exc}") from exc

    @mcp.tool()
    def lookup_dependency(name: str) -> dict[str, Any]:
        """view/procedure 反向依赖(Oracle ALL_DEPENDENCIES)。离线返空 + note。"""
        try:
            return lineage_tools.lookup_dependency(
                app_ctx.engine, name=name, router=app_ctx.oracle_router())
        except Exception as exc:
            raise ToolError(f"lookup_dependency failed: {exc}") from exc

    @mcp.tool()
    def lookup_sequence(name: str) -> dict[str, Any]:
        """sequence 元数据(Oracle ALL_SEQUENCES)+ 本地代码引用。"""
        try:
            return lineage_tools.lookup_sequence(
                app_ctx.engine, name=name, router=app_ctx.oracle_router())
        except Exception as exc:
            raise ToolError(f"lookup_sequence failed: {exc}") from exc

    @mcp.tool()
    def search_sql(pattern: str, limit: int = 20) -> list[dict[str, Any]]:
        """grep 已恢复 SQL 模板(sql_templates.sql_text)字面包含 pattern。纯本地。"""
        try:
            return lineage_tools.search_sql(app_ctx.engine, pattern=pattern, limit=limit)
        except Exception as exc:
            raise ToolError(f"search_sql failed: {exc}") from exc

    # ------------------------------------------------------------------ lineage.dataflow(1)

    @mcp.tool()
    def trace_method_dataflow(source_path: str) -> list[dict[str, Any]]:
        """方法所在文件 -> 触及的表(三路 fallback:lineage_evidence / sql_templates)。"""
        try:
            return _trace_method_dataflow(app_ctx.engine, source_path=source_path)
        except Exception as exc:
            raise ToolError(f"trace_method_dataflow failed: {exc}") from exc

    # ------------------------------------------------------------------ config 维(5)

    @mcp.tool()
    def lookup_config(config_key: str) -> dict[str, Any]:
        """配置项查询(config_key 精确 -> key_path 子串)。自由文本经 profile 敏感词脱敏。"""
        try:
            return config_tools.lookup_config(
                app_ctx.engine, config_key=config_key,
                patterns=_sensitive_patterns(app_ctx), salt=_config_salt(app_ctx))
        except Exception as exc:
            raise ToolError(f"lookup_config failed: {exc}") from exc

    @mcp.tool()
    def lookup_rule(rule_set: str) -> dict[str, Any]:
        """规则集查询(name/id 命中 + rule_bindings)。evidence 经 profile 敏感词脱敏。"""
        try:
            return config_tools.lookup_rule(
                app_ctx.engine, rule_set=rule_set,
                patterns=_sensitive_patterns(app_ctx))
        except Exception as exc:
            raise ToolError(f"lookup_rule failed: {exc}") from exc

    @mcp.tool()
    def trace_config_impact(entity_key: str) -> dict[str, Any]:
        """配置 entity -> 直接绑定(class/method/table)。evidence 经 profile 敏感词脱敏。"""
        try:
            return config_tools.trace_config_impact(
                app_ctx.engine, entity_key=entity_key,
                patterns=_sensitive_patterns(app_ctx))
        except Exception as exc:
            raise ToolError(f"trace_config_impact failed: {exc}") from exc

    @mcp.tool()
    def explain_rule_logic(rule_set_id: str) -> dict[str, Any]:
        """规则集结构/绑定/示例列(clauses Scope A v1 空)。文本经 profile 敏感词脱敏。"""
        try:
            return config_tools.explain_rule_logic(
                app_ctx.engine, rule_set_id=rule_set_id,
                patterns=_sensitive_patterns(app_ctx))
        except Exception as exc:
            raise ToolError(f"explain_rule_logic failed: {exc}") from exc

    @mcp.tool()
    def diff_config(source_id: str, env_a: str, env_b: str) -> dict[str, Any]:
        """两环境配置快照的 key 级 diff。缺一侧快照 -> note='snapshot_missing'。"""
        try:
            return config_tools.diff_config(
                app_ctx.engine, source_id=source_id, env_a=env_a, env_b=env_b,
                patterns=_sensitive_patterns(app_ctx))
        except Exception as exc:
            raise ToolError(f"diff_config failed: {exc}") from exc

    # ------------------------------------------------------------------ code(4)

    @mcp.tool()
    def search_code(query: str, kind: str = "") -> list[dict[str, Any]]:
        """单 query 查 Java 符号(04b 投影查表平替 workspaceSymbol)。kind 非空时只保留该 01-Kind。"""
        try:
            return search_code_query(app_ctx.searcher, query=query, kind=kind)
        except Exception as exc:
            raise ToolError(f"search_code failed: {exc}") from exc

    @mcp.tool()
    def lookup_calls(method_fqn: str, direction: str = "callees", depth: int = 1) -> dict[str, Any]:
        """查 code_calls 调用边(投影, 零 JDT)。method_fqn 裸名/带签名均可;
        裸名多重载 -> 报错列出全部带签名候选。direction=callers|callees, depth<=2。"""
        ci = app_ctx.profile.code_index
        try:
            return _lookup_calls(app_ctx.engine, method_fqn=method_fqn, direction=direction,
                                 depth=min(depth, ci.lookup_calls_max_depth),
                                 fanout=ci.lookup_calls_fanout, max_rows=ci.lookup_calls_max_rows)
        except AmbiguousMethodFqn as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"lookup_calls failed: {exc}") from exc

    @mcp.tool()
    def read_symbol(fqn: str) -> dict[str, Any]:
        """按 FQN 切源码(四护栏: FQN-only / resolve 前缀校验 / cap / 脱敏+redacted)。
        方法 FQN 裸名/带签名均可; 裸名多重载 -> 报错列出全部带签名候选。"""
        p = app_ctx.profile
        try:
            return get_symbol_source(
                app_ctx.engine, repo_root=repo_root(p), source_roots=resolve_source_roots(p),
                fqn=fqn, max_lines=p.code_index.read_symbol_max_lines,
                sensitive_patterns=_sensitive_patterns(app_ctx))
        except AmbiguousMethodFqn as exc:
            raise ToolError(str(exc)) from exc
        except SymbolNotFound as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"read_symbol failed: {exc}") from exc

    @mcp.tool()
    def search_source(query: str, mode: Literal["literal", "regex"] = "literal",
                      case_sensitive: bool = False, context_lines: int = 0,
                      file_extensions: list[str] | None = None) -> dict[str, Any]:
        """原始源码文本检索(服务端 rg, host 零 shell)。补符号索引/恢复 SQL 盲区:
        框架字符串派发、内联字面量、配置文件文本。只搜 profile source_roots, 脱敏返回。
        命中=text-hit 弱证据(.java 投影内回填 enclosing FQN, host 可 read_symbol 升级)。
        mode: literal(默认,字面)| regex(rg 线性引擎)。context_lines<=5。caps 服务端固定。"""
        p = app_ctx.profile
        try:
            return _search_source(
                repo_root=repo_root(p), source_roots=resolve_source_roots(p),
                query=query, mode=mode, case_sensitive=case_sensitive,
                context_lines=context_lines, file_extensions=file_extensions,
                exclude_dirs=list(p.code.exclude_dirs),
                engine=app_ctx.engine, sensitive_patterns=_sensitive_patterns(app_ctx))
        except RipgrepUnavailable as exc:
            raise ToolError(
                f"search_source unavailable: {exc}. 安装 ripgrep(rg) 后重试。") from exc
        except Exception as exc:
            raise ToolError(f"search_source failed: {exc}") from exc

    # ------------------------------------------------------------------ rag(1)

    @mcp.tool()
    def rag_search(
        queries: dict[str, str], corpora: list[str], top_k: int = 10
    ) -> list[dict[str, Any]]:
        """业务文档 RAG 检索(corpora 白名单由 middleware 校验)。返回命中段落。"""
        try:
            return _rag_search(
                app_ctx.rag_provider, queries=queries, corpora=corpora, top_k=top_k,
                corpus_prefixes=app_ctx.profile.config.corpus_subset_prefixes)
        except Exception as exc:
            raise ToolError(f"rag_search failed: {exc}") from exc
