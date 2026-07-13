"""MyBatis mapper 摄入封装层(多方言 spec 4.6 + 附录 E; 补丁 2/3 宿主)。

独立地基, 零耦合: 不 import lineage pipeline / source_scan / sql_recover ——
接线期(L3)统一挂现有 sqlglot 解析链与 sql_recover 兜底。本层职责:

  识别 is_mybatis_mapper(与 config_dim 共用 util.mybatis_sniff, spec E.4)
  -> 展开 expand_mappers(vendored mybatis_mapper2sql + 跨文件 <sql> 片段注入,
     spec E.3.2 补丁 2: 上游 create_mapper 单文件作用域, convert_include 用
     mapper dict 查 refid, 故把全仓片段并进每个 mapper 的 dict 即可, 不 fork 上游)
  -> 清洗与抽表 strip_dynamic_markers / extract_tables(spec E.3.3 补丁 3:
     sqlglot 严格解析主路径 + 正则容错兜底)。

choose 全分支并集语义(spec E.1 核心): 上游 native=False 把所有 when/otherwise
分支正文拼进同一条展开产物, 分支间夹 `-- if(test)` / `-- otherwise` 内联标记。
分支体是裸表名时剥标记后 SQL 必然非法("from A r B r C r"), 正则兜底只抓得到
首分支 —— 故 extract_tables 吃**带标记原文**, 把标记当分支边界结构信号:
上一个显著关键字是 from/join/into/update 时, 标记后的首标识符也按表收。
"""
from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import sqlglot
from sqlglot import exp

from contextos.lineage._vendor.mybatis_mapper2sql.generate import (
    create_mapper,
    get_child_statement,
)
from contextos.util.mybatis_sniff import sniff_mybatis_mapper_text

logger = logging.getLogger(__name__)

_STATEMENT_KINDS = ("select", "insert", "update", "delete")

# 展开时的 sqlparse 排版参数: 保留注释(-- if(...) 分支标记是下游的分支边界
# 信号, 由 extract_tables 剥), 只做缩进美化(超大 mapper 美化失败由 vendored
# patch 1 短路返原文)。
_FORMAT_KWARGS = {"reindent": True, "strip_comments": False}

_NAMESPACE_RE = re.compile(r"<mapper\b[^>]*?\bnamespace\s*=\s*[\"']([^\"']*)[\"']")

# 分支标记: 上游 convert_if / convert_choose_when_otherwise 注入的内联注释
_IF_MARKER_RE = re.compile(r"--\s*if\(")
_OTHERWISE_MARKER_RE = re.compile(r"--\s*otherwise\b")

# 兜底正则(参考 lineage/sql_recover.py 的容错思路, 不 import 保持零耦合)
_TABLE_AFTER_KW_RE = re.compile(
    r"\b(?:from|join|into|update)\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)",
    re.IGNORECASE,
)
_LAST_KW_RE = re.compile(
    r"\b(from|join|into|update|where|select|group|order|on|and|or|set|values"
    r"|having|limit|union|when|then|else|end|case|left|right|inner|outer|cross)\b",
    re.IGNORECASE,
)
_LEADING_IDENT_RE = re.compile(r"^\s*([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)")
# 分支边界启发式收表时, 首标识符若是 SQL 关键字则不收(防 "from t <标记> where ..."
# 把 where 当表)
_IDENT_STOP_WORDS = frozenset({
    "and", "or", "not", "exists", "where", "select", "from", "join", "inner",
    "left", "right", "outer", "cross", "on", "set", "values", "group", "order",
    "having", "limit", "union", "when", "then", "else", "end", "case", "by",
    "distinct", "all", "as", "in", "is", "null", "like", "between",
})


@dataclass
class MapperStatement:
    """一条 mapper 语句的展开产物(接线期由 L3 转 RecoveredSqlCandidate)。"""
    namespace: str        # <mapper namespace>; 与语句 id 直拼即方法 FQN(spec E.5)
    statement_id: str     # <select/insert/update/delete id>
    sql_kind: str         # select / insert / update / delete
    raw_sql: str          # 展开后文本(保留 -- if(...) 分支标记, 供抽表与人读分支条件)
    source_path: str      # mapper 文件路径(str(传入 path) 原样)
    line: int | None = None  # 语句起始行(尽力: 按 <tag ... id="..."> 原文定位)


def _read_text(path: Path) -> str:
    """mapper 文件容错读取: utf-8 -> gbk -> utf-8 replace(中文老工程常见 GBK)。"""
    data = path.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _statement_line(text: str, tag: str, stmt_id: str) -> int | None:
    m = re.search(
        rf"<{tag}\b[^>]*\bid\s*=\s*[\"']{re.escape(stmt_id)}[\"']", text)
    if not m:
        return None
    return text.count("\n", 0, m.start()) + 1


def is_mybatis_mapper(path: str | Path) -> bool:
    """spec E.4: 按文件内容识别 mapper(DTD 声明或根标签), 不按目录约定。

    判定实现与 config_dim xml_mybatis_parser 共用 util.mybatis_sniff(MUST:
    两处口径不漂移)。target/-bak 等目录排除是扫描层职责, 不在本函数。
    """
    try:
        text = _read_text(Path(path))
    except OSError:
        return False
    return sniff_mybatis_mapper_text(text)


def expand_mappers(paths: Sequence[str | Path]) -> list[MapperStatement]:
    """两趟展开(spec E.3.2 补丁 2): 先全量收 <sql> 片段, 再逐 mapper 展开。

    片段注册双键: 裸 id(同名先到先得)+ namespace 全限定 id; 展开时本文件
    条目最后并入(local wins)。单文件解析失败 / 单语句展开失败只跳过该项
    并告警, 不阻断其余(摄入层容错, 质量问题接线期按证据分级)。
    """
    parsed: list[tuple[Path, str, str, dict]] = []
    fragments: dict[str, object] = {}
    for raw_path in paths:
        path = Path(raw_path)
        try:
            text = _read_text(path)
            mapper, _ = create_mapper(xml_raw_text=text)
        except Exception:
            logger.warning("mybatis mapper 解析失败, 跳过: %s", path)
            continue
        ns_match = _NAMESPACE_RE.search(text)
        namespace = ns_match.group(1) if ns_match else ""
        parsed.append((path, text, namespace, mapper))
        for frag_id, el in mapper.items():
            if frag_id is None or getattr(el, "tag", None) != "sql":
                continue
            if namespace:
                fragments.setdefault(f"{namespace}.{frag_id}", el)
            fragments.setdefault(frag_id, el)

    results: list[MapperStatement] = []
    for path, text, namespace, mapper in parsed:
        merged = dict(fragments)
        merged.update(mapper)  # 本文件条目优先(local wins)
        for stmt_id, el in mapper.items():
            if stmt_id is None or el.tag not in _STATEMENT_KINDS:
                continue
            try:
                raw_sql = get_child_statement(merged, stmt_id, **_FORMAT_KWARGS)
            except Exception:
                logger.warning("mybatis 语句展开失败, 跳过: %s#%s", path, stmt_id)
                continue
            results.append(MapperStatement(
                namespace=namespace,
                statement_id=stmt_id,
                sql_kind=el.tag,
                raw_sql=raw_sql,
                source_path=str(path),
                line=_statement_line(text, el.tag, stmt_id),
            ))
    return results


def _split_at_markers(sql: str) -> list[str]:
    """按 -- if(...) / -- otherwise 分支标记切段, 标记本体被吃掉。

    if 标记的 test 条件里可有嵌套括号(a.size() > 0), 必须括号配平扫描,
    不能非贪婪到首个 ')' 或整行剥(标记后同行可能紧跟下一分支的表名);
    行内未配平则剥到行尾兜底。
    """
    segments: list[str] = []
    i = 0
    while True:
        m_if = _IF_MARKER_RE.search(sql, i)
        m_ot = _OTHERWISE_MARKER_RE.search(sql, i)
        marks = [m for m in (m_if, m_ot) if m is not None]
        if not marks:
            segments.append(sql[i:])
            return segments
        m = min(marks, key=lambda x: x.start())
        segments.append(sql[i:m.start()])
        j = m.end()
        if m is m_if:
            depth = 1
            while j < len(sql) and depth:
                ch = sql[j]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "\n":
                    break
                j += 1
        i = j


def strip_dynamic_markers(sql: str) -> str:
    """spec E.3.3 补丁 3: 剥上游注入的 -- if(...) / -- otherwise 内联标记。

    标记位置换单个空格占位(防两侧 token 粘连); 无标记原文原样返回。
    """
    segments = _split_at_markers(sql)
    if len(segments) == 1:
        return sql
    return " ".join(segments)


def extract_tables(raw_sql: str, sqlglot_dialect: str) -> set[str]:
    """spec E.3.3: 剥标记后 sqlglot 严格解析抽表; 失败退正则容错兜底。

    输入应为 expand_mappers 产出的**带标记原文**(标记是 choose 分支边界的
    结构信号, 兜底路径靠它收全裸表名分支; 见模块 docstring)。dialect 由
    调用方传(接线期按 profile database.type 映射, 本层不做默认值)。
    返回不带 schema 前缀的表名集合(a.b -> b); CTE 别名不计。
    """
    segments = _split_at_markers(raw_sql)
    cleaned = " ".join(segments) if len(segments) > 1 else raw_sql
    try:
        statements = sqlglot.parse(cleaned, dialect=sqlglot_dialect)
        tables: set[str] = set()
        cte_aliases: set[str] = set()
        for stmt in statements:
            if stmt is None:
                continue
            for cte in stmt.find_all(exp.CTE):
                cte_aliases.add(cte.alias_or_name)
            for t in stmt.find_all(exp.Table):
                if t.name:
                    tables.add(t.name)
        return tables - cte_aliases
    except Exception:
        pass
    return _regex_tables(segments)


def _regex_tables(segments: list[str]) -> set[str]:
    """容错兜底: from/join/into/update 后标识符 + choose 分支边界启发式。

    启发式(spec E.1 裸表名分支形态): 扫描到某分支标记边界时, 若此前最近的
    显著关键字是 from/join/into/update, 则下一段的首标识符也按表收
    (分支段自身无关键字时状态延续, 三分支以上也收得全)。
    """
    tables: set[str] = set()
    cleaned = " ".join(segments)
    for m in _TABLE_AFTER_KW_RE.finditer(cleaned):
        tables.add(m.group(1).rsplit(".", 1)[-1])
    last_kw: str | None = None
    for idx, seg in enumerate(segments):
        if idx > 0 and last_kw in ("from", "join", "into", "update"):
            lead = _LEADING_IDENT_RE.match(seg)
            if lead:
                ident = lead.group(1)
                if ident.lower() not in _IDENT_STOP_WORDS:
                    tables.add(ident.rsplit(".", 1)[-1])
        for kw_match in _LAST_KW_RE.finditer(seg):
            last_kw = kw_match.group(1).lower()
    return tables
