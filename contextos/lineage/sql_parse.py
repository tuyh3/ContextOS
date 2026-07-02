"""Layer 5-6: SQL 预处理 + sqlglot 解析(移植 LP sql_preprocess.py + sql_parse.py)。

preprocess: 占位符归一(确定性, 不激进重写)。
parse: sqlglot AST -> 8 relation_type + sequence refs; AST 失败 -> regex fallback。
8 取值(01 §3.0.1): INSERT_SELECT/UPDATE_FROM/DELETE_FROM/MERGE/JOIN/WHERE_EQ/SUBQUERY/EXISTS。
"""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from contextos.lineage.models import ParsedRelation, SequenceRef


def preprocess_sql(sql: str) -> str:
    """确定性占位符归一(原始 SQL 由调用方留作 evidence)。"""
    sql = sql.rstrip(";").strip()
    sql = re.sub(r"\{([A-Z][A-Z0-9_]*)\}", r"\1", sql)   # {UM_EC_VPN} -> UM_EC_VPN
    sql = re.sub(r"(%[sS])\b", "", sql)                    # AM_BILL_AR_%s -> AM_BILL_AR_
    sql = re.sub(r"\$\{(\w+)\}", r":\1", sql)              # ${var} -> :var
    sql = re.sub(r"#\{(\w+)\}", r":\1", sql)               # #{var} -> :var
    sql = re.sub(r"(?<!\w)\?(?!\w)", ":p", sql)            # ? -> :p
    return sql.strip()


def parse_sql(sql_text: str) -> tuple[list[ParsedRelation], list[SequenceRef], str | None]:
    """返回 (relations, seq_refs, error)。error=None 表示成功。"""
    sql = preprocess_sql(sql_text)
    if not sql:
        return [], [], "empty SQL"
    try:
        tree = sqlglot.parse_one(sql, dialect="oracle")
    except Exception:
        relations = _regex_fallback(sql)
        seq_refs = _regex_sequence_refs(sql)
        if relations or seq_refs:
            return relations, seq_refs, None
        return [], [], "parse failed (AST + regex)"
    if tree is None:
        return [], [], "parse failed (empty AST)"
    return _extract_relations(tree), _extract_sequence_refs(tree), None


def _extract_relations(tree) -> list[ParsedRelation]:
    relations: list[ParsedRelation] = []
    cte_names = {c.alias for c in tree.find_all(exp.CTE)}
    all_tables: dict[str, tuple[str, str]] = {}
    real_table_names: set[str] = set()
    for t in tree.find_all(exp.Table):
        name = t.name
        if name.upper() in _SQL_KEYWORDS or name in cte_names:
            continue
        alias = t.alias or name
        schema = t.db or ""
        all_tables[alias] = (name, schema)
        all_tables[name] = (name, schema)
        real_table_names.add(name)
    if not all_tables:
        return relations

    # write_target 身份锚含 schema(review HIGH: 显式 schema 的目标表 owner 不丢)
    write_target = None
    if isinstance(tree, exp.Insert):
        if tree.this and isinstance(tree.this, exp.Table):
            write_target = tree.this.name
            _add_write_relations(relations, write_target, tree.this.db or "", tree, cte_names, "INSERT_SELECT")
        else:  # INSERT INTO T(col) ... -> Schema 节点
            tbl = tree.this.find(exp.Table) if tree.this else None
            if tbl:
                write_target = tbl.name
                _add_write_relations(relations, write_target, tbl.db or "", tree, cte_names, "INSERT_SELECT")
    elif isinstance(tree, exp.Update):
        if tree.this and isinstance(tree.this, exp.Table):
            write_target = tree.this.name
            _add_write_relations(relations, write_target, tree.this.db or "", tree, cte_names, "UPDATE_FROM")
    elif isinstance(tree, exp.Delete):
        if tree.this and isinstance(tree.this, exp.Table):
            write_target = tree.this.name
            _add_write_relations(relations, write_target, tree.this.db or "", tree, cte_names, "DELETE_FROM")
    elif isinstance(tree, exp.Merge):
        if tree.this and isinstance(tree.this, exp.Table):
            write_target = tree.this.name
            _add_write_relations(relations, write_target, tree.this.db or "", tree, cte_names, "MERGE")
    elif isinstance(tree, exp.Create):
        if tree.expression and isinstance(tree.expression, exp.Select) and tree.this:
            wt = tree.this.name if hasattr(tree.this, "name") else None
            if wt:
                write_target = wt
                # CTAS 语义 = CREATE + 写入, 归 INSERT_SELECT(8 类之一);
                # 原产 "CTAS" 第 9 种 -> assemble 退化 WHERE_EQ(既有漂移, 见 design §6.1)。
                _add_write_relations(relations, wt, getattr(tree.this, "db", "") or "",
                                     tree.expression, cte_names, "INSERT_SELECT")

    _extract_join_relations(tree, relations, all_tables, real_table_names)
    _extract_where_relations(tree, relations, all_tables, real_table_names)
    _extract_subquery_relations(tree, relations, cte_names)
    return relations


def _add_write_relations(relations, write_target, write_schema, tree, cte_names, relation_type):
    for t in tree.find_all(exp.Table):
        name = t.name
        # 自连过滤按 (schema, name): UPC.T 的 SELECT FROM SEC.T 不是自连(review HIGH 完整性)
        if (name == write_target and (t.db or "") == write_schema) \
                or name.upper() in _SQL_KEYWORDS or name in cte_names:
            continue
        relations.append(ParsedRelation(
            src_table=name, dst_table=write_target, relation_type=relation_type,
            lineage_type="DIRECT", src_schema=t.db or "", dst_schema=write_schema,
            is_write_target=False))


def _extract_join_relations(tree, relations, all_tables, real_table_names):
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if not on:
            continue
        for eq in on.find_all(exp.EQ):
            pair = _extract_eq_pair(eq, all_tables, real_table_names)
            # 自连过滤按 (schema, name): UPC.COMMON_T vs SEC.COMMON_T 是不同表(裁决 5)
            if pair and (pair[2], pair[0]) != (pair[5], pair[3]):
                relations.append(ParsedRelation(
                    src_table=pair[0], src_col=pair[1], src_schema=pair[2],
                    dst_table=pair[3], dst_col=pair[4], dst_schema=pair[5],
                    relation_type="JOIN", lineage_type="INDIRECT"))


def _extract_where_relations(tree, relations, all_tables, real_table_names):
    where = tree.find(exp.Where)
    if not where:
        return
    for eq in where.find_all(exp.EQ):
        if _is_inside_subquery(eq, where):
            continue
        pair = _extract_eq_pair(eq, all_tables, real_table_names)
        if pair and (pair[2], pair[0]) != (pair[5], pair[3]):
            relations.append(ParsedRelation(
                src_table=pair[0], src_col=pair[1], src_schema=pair[2],
                dst_table=pair[3], dst_col=pair[4], dst_schema=pair[5],
                relation_type="WHERE_EQ", lineage_type="INDIRECT"))


def _is_inside_subquery(node, stop_at) -> bool:
    current = node.parent
    while current and current is not stop_at:
        if isinstance(current, (exp.Subquery, exp.Exists)):
            return True
        current = current.parent
    return False


def _extract_subquery_relations(tree, relations, cte_names):
    # 按 (schema, name) 追踪(review HIGH 完整性): 同名跨 schema 不被当同表过滤 + 关系带 schema
    def _tbls(node) -> set[tuple[str, str]]:
        return {(t.db or "", t.name) for t in node.find_all(exp.Table)
                if t.name not in cte_names and t.name.upper() not in _SQL_KEYWORDS}
    from_clause = tree.find(exp.From)
    outer_tables = _tbls(from_clause) if from_clause else set()
    nested_nodes = []
    for sq in tree.find_all(exp.Subquery):
        is_exists = isinstance(sq.parent, exp.Exists)
        nested_nodes.append((sq, "EXISTS" if is_exists else "SUBQUERY"))
    for ex in tree.find_all(exp.Exists):
        if not list(ex.find_all(exp.Subquery)):
            nested_nodes.append((ex, "EXISTS"))
    for nested, rel_type in nested_nodes:
        inner_tables = _tbls(nested)
        for o_schema, o_name in outer_tables:
            for i_schema, i_name in inner_tables - outer_tables:
                relations.append(ParsedRelation(
                    src_table=i_name, src_schema=i_schema,
                    dst_table=o_name, dst_schema=o_schema,
                    relation_type=rel_type, lineage_type="INDIRECT"))


def _extract_eq_pair(eq_node, all_tables, real_table_names):
    """返回 (left_tbl, left_col, left_schema, right_tbl, right_col, right_schema) 或 None。

    review HIGH: 透传 all_tables 里捕获的 schema(原来只返回 name, JOIN/WHERE 关系丢 owner)。"""
    left, right = eq_node.left, eq_node.right
    if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
        return None
    left_tbl, left_col = left.table or "", left.name
    right_tbl, right_col = right.table or "", right.name
    left_schema = right_schema = ""
    if left_tbl in all_tables:
        left_tbl, left_schema = all_tables[left_tbl]
    if right_tbl in all_tables:
        right_tbl, right_schema = all_tables[right_tbl]
    if not left_tbl or not right_tbl:
        return None
    if left_tbl not in real_table_names or right_tbl not in real_table_names:
        return None
    return (left_tbl, left_col, left_schema, right_tbl, right_col, right_schema)


def _extract_sequence_refs(tree) -> list[SequenceRef]:
    refs = []
    for col in tree.find_all(exp.Column):
        if col.name.upper() in ("NEXTVAL", "CURRVAL"):
            seq_name = col.table or ""
            if seq_name:
                refs.append(SequenceRef(sequence_name=seq_name, ref_type=col.name.upper(),
                                        context_table=_get_insert_target(tree)))
    return refs


def _get_insert_target(tree) -> str:
    if not isinstance(tree, exp.Insert) or not tree.this:
        return ""
    target = tree.this
    if isinstance(target, exp.Table):
        return target.name
    tbl = target.find(exp.Table)
    if tbl:
        return tbl.name
    if hasattr(target, "this") and hasattr(target.this, "name"):
        return target.this.name
    return ""


_SQL_KEYWORDS = {
    "DUAL", "SYS", "SYSTEM", "PUBLIC", "DBMS_OUTPUT", "UTL_FILE",
    "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "FULL", "CROSS",
    "INNER", "OUTER", "ON", "AND", "OR", "NOT", "IN", "EXISTS", "BETWEEN",
    "LIKE", "IS", "NULL", "CASE", "WHEN", "THEN", "ELSE", "END", "AS",
    "GROUP", "ORDER", "BY", "HAVING", "UNION", "ALL", "INSERT", "INTO",
    "VALUES", "UPDATE", "SET", "DELETE", "CREATE", "ALTER", "DROP",
    "TABLE", "INDEX", "VIEW", "WITH", "DISTINCT",
}

_RE_TABLE_REF = re.compile(
    r"(?:FROM|(?:LEFT|RIGHT|FULL|CROSS|INNER|OUTER)?\s*(?:OUTER\s+)?JOIN)\s+"
    r"([\w.]+?)(?:\s+(\w+))?\s*(?:ON\b|,|\s+(?:LEFT|RIGHT|FULL|CROSS|INNER|OUTER|JOIN|WHERE|GROUP|ORDER|$))",
    re.IGNORECASE)
_RE_EQUI = re.compile(r"(\w+)\.(\w+)\s*(?:\(\+\))?\s*=\s*(\w+)\.(\w+)\s*(?:\(\+\))?", re.IGNORECASE)
_RE_SEQ_REF = re.compile(r"(\w+)\.(NEXTVAL|CURRVAL)", re.IGNORECASE)
_RE_INSERT_INTO = re.compile(r"INSERT\s+INTO\s+([\w.]+)", re.IGNORECASE)


def _regex_fallback(sql: str) -> list[ParsedRelation]:
    relations: list[ParsedRelation] = []
    upper = sql.upper()
    alias_map: dict[str, str] = {}
    for m in _RE_TABLE_REF.finditer(sql):
        tbl = m.group(1).split(".")[-1]
        alias = m.group(2) or tbl
        if tbl.upper() not in _SQL_KEYWORDS:
            alias_map[alias] = tbl
    if len(alias_map) < 2:
        return relations
    seen = set()
    for m in _RE_EQUI.finditer(sql):
        left_tbl = alias_map.get(m.group(1))
        right_tbl = alias_map.get(m.group(3))
        if not left_tbl or not right_tbl or left_tbl == right_tbl:
            continue
        if left_tbl.upper() in _SQL_KEYWORDS or right_tbl.upper() in _SQL_KEYWORDS:
            continue
        rel_type = "JOIN" if "JOIN" in upper else "WHERE_EQ"
        key = tuple(sorted([left_tbl, right_tbl]))
        if key in seen:
            continue
        seen.add(key)
        relations.append(ParsedRelation(
            src_table=left_tbl, src_col=m.group(2), dst_table=right_tbl, dst_col=m.group(4),
            relation_type=rel_type, lineage_type="INDIRECT"))
    return relations


def _regex_sequence_refs(sql: str) -> list[SequenceRef]:
    refs = []
    insert_m = _RE_INSERT_INTO.search(sql)
    context_table = insert_m.group(1).split(".")[-1] if insert_m else ""
    for m in _RE_SEQ_REF.finditer(sql):
        seq_name = m.group(1)
        if seq_name.upper() not in _SQL_KEYWORDS:
            refs.append(SequenceRef(sequence_name=seq_name, ref_type=m.group(2).upper(),
                                    context_table=context_table))
    return refs
