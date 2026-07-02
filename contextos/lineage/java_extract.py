"""Layer 3: tree-sitter-java 抽取 Java 内候选 SQL(移植 LP java_extract.py + §9 day-1)。

day-1 改进(不依赖 04b):
  §9.3 branch_detected: append 在 if/else/switch/for 祖先内 -> 标记 + confidence=low
  §9.1 local_var: 方法体内 def-use 链追源头字面量
  §9.4 String.format/replace 全字面量 -> 静态执行 (recovery_mode=literal)
延后(需 04b java_fields): §9.2 static_const 类级常量跨方法引用(留 ${?})。
"""
from __future__ import annotations

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from contextos.lineage.models import RecoveredSqlCandidate

_JAVA_LANG = Language(tsjava.language())
_parser = Parser(_JAVA_LANG)

_SQL_HINTS = {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "FROM", "JOIN"}
_SINK_METHODS = {
    "prepareStatement", "createQuery", "executeQuery", "executeUpdate",
    "execute", "prepareCall", "nativeQuery", "createNativeQuery",
    "createSQLQuery", "addBatch", "setSql", "sqlQuery",
}
_BRANCH_NODE_TYPES = {"if_statement", "switch_statement", "switch_expression",
                      "for_statement", "enhanced_for_statement", "while_statement",
                      "ternary_expression"}


def extract_sql_from_java(content: str, source_path: str) -> list[RecoveredSqlCandidate]:
    upper = content.upper()
    if not any(kw in upper for kw in _SQL_HINTS):
        return []
    content_bytes = content.encode("utf-8")
    root = _parser.parse(content_bytes).root_node
    results: list[RecoveredSqlCandidate] = []
    _extract_concat_assignments(root, content_bytes, source_path, results)
    _extract_string_builder(root, content_bytes, source_path, results)
    return results


# ---------- 变量赋值(literal / concat / local_var / String.format) ----------

def _extract_concat_assignments(node, cb, source_path, results):
    if node.type == "variable_declarator":
        name_node = node.child_by_field_name("name")
        value_node = node.child_by_field_name("value")
        if name_node and value_node:
            var_name = _node_text(name_node, cb)
            method_body = _find_enclosing_method(node)
            local_vars = _collect_local_string_literals(method_body, cb) if method_body else {}
            local_vars.pop(var_name, None)  # 不拿自身
            sql, mode = _resolve_value(value_node, cb, local_vars)
            if sql and _looks_like_sql(sql):
                if mode == "literal" and value_node.type != "string_literal":
                    pass  # format/replace 解析出的字面等价
                has_sink = bool(method_body) and _var_reaches_sink(var_name, method_body, cb)
                confidence = "medium" if (mode in ("literal", "local_var") or has_sink) else "low"
                results.append(RecoveredSqlCandidate(
                    source_path=source_path,
                    line_start=node.start_point[0] + 1, line_end=node.end_point[0] + 1,
                    container=_find_container(node, cb), sql_text=sql,
                    recovery_mode=mode, confidence=confidence))
    if node.type == "assignment_expression":
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left and right:
            method_body = _find_enclosing_method(node)
            local_vars = _collect_local_string_literals(method_body, cb) if method_body else {}
            sql, mode = _resolve_value(right, cb, local_vars)
            if sql and _looks_like_sql(sql):
                results.append(RecoveredSqlCandidate(
                    source_path=source_path,
                    line_start=node.start_point[0] + 1, line_end=node.end_point[0] + 1,
                    container=_find_container(node, cb), sql_text=sql,
                    recovery_mode=mode, confidence="low"))
    for child in node.children:
        _extract_concat_assignments(child, cb, source_path, results)


def _resolve_value(value_node, cb, local_vars: dict[str, str]) -> tuple[str | None, str]:
    """返回 (sql_text, recovery_mode)。

    优先级: 纯字面量=literal / String.format|replace 全字面量=literal /
    含 local_var 解析出源头=local_var / 其余拼接=concat。
    """
    if value_node.type == "string_literal":
        return _string_literal_value(value_node, cb), "literal"
    # String.format(...) / x.replace(...) 全字面量
    fmt = _try_static_format(value_node, cb)
    if fmt is not None:
        return fmt, "literal"
    # 拼接: 收集各部分, 用 local_vars 解析变量引用
    used_local = [False]
    sql = _collect_string_parts(value_node, cb, local_vars, used_local)
    if sql is None:
        return None, "concat"
    mode = "local_var" if used_local[0] else "concat"
    return sql, mode


def _collect_string_parts(node, cb, local_vars, used_local) -> str | None:
    """递归收集拼接表达式各部分。identifier 命中 local_vars -> 替换(标记 used_local)。"""
    if node.type == "string_literal":
        return _string_literal_value(node, cb)
    if node.type == "identifier":
        name = _node_text(node, cb)
        if name in local_vars:
            used_local[0] = True
            return local_vars[name]
        return "${?}"
    if node.type == "binary_expression":
        children = [c for c in node.children if c.type != "+"]
        if len(children) >= 2:
            left = _collect_string_parts(children[0], cb, local_vars, used_local)
            right = _collect_string_parts(children[-1], cb, local_vars, used_local)
            if left is None and right is None:
                return None
            return (left or "${?}") + (right or "${?}")
        return None
    if node.type == "parenthesized_expression":
        for child in node.children:
            if child.type not in ("(", ")"):
                return _collect_string_parts(child, cb, local_vars, used_local)
    # 方法调用 / 其它非字面量 -> 占位
    return "${?}"


def _try_static_format(node, cb) -> str | None:
    """String.format("...%d", 100) / "...".replace("@@T@@","X") 全字面量 -> 静态求值。"""
    if node.type != "method_invocation":
        return None
    name_node = node.child_by_field_name("name")
    if not name_node:
        return None
    method = _node_text(name_node, cb)
    args = node.child_by_field_name("arguments")
    if not args:
        return None
    arg_nodes = [a for a in args.children if a.type not in (",", "(", ")")]
    if method == "format" and arg_nodes:
        # 第一个参数是格式串字面量, 其余全字面量
        fmt = _string_literal_value(arg_nodes[0], cb)
        if fmt is None:
            return None
        rest = [_literal_arg_value(a, cb) for a in arg_nodes[1:]]
        if any(v is None for v in rest):
            return None
        # 把 %s/%d/%S 顺序替换为字面量值
        import re as _re
        out, idx = [], 0
        for piece in _re.split(r"(%[sSdD])", fmt):
            if _re.fullmatch(r"%[sSdD]", piece) and idx < len(rest):
                out.append(str(rest[idx])); idx += 1
            else:
                out.append(piece)
        return "".join(out)
    if method == "replace" and len(arg_nodes) == 2:
        obj = node.child_by_field_name("object")
        base = _string_literal_value(obj, cb) if obj is not None else None
        a0 = _string_literal_value(arg_nodes[0], cb)
        a1 = _string_literal_value(arg_nodes[1], cb)
        if base is not None and a0 is not None and a1 is not None:
            return base.replace(a0, a1)
    return None


def _literal_arg_value(node, cb) -> str | None:
    """字面量参数的值(string_literal / decimal_integer_literal)。"""
    if node.type == "string_literal":
        return _string_literal_value(node, cb)
    if node.type in ("decimal_integer_literal", "hex_integer_literal", "decimal_floating_point_literal"):
        return _node_text(node, cb)
    return None


def _string_literal_value(node, cb) -> str | None:
    if node is None or node.type != "string_literal":
        return None
    text = _node_text(node, cb)
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return None


def _collect_local_string_literals(method_node, cb) -> dict[str, str]:
    """收集方法体内 `String x = "literal";` 的 name->literal(供 §9.1 local_var def-use)。"""
    out: dict[str, str] = {}
    if method_node is None:
        return out

    def _walk(n):
        if n.type == "variable_declarator":
            nm = n.child_by_field_name("name")
            val = n.child_by_field_name("value")
            if nm and val and val.type == "string_literal":
                lit = _string_literal_value(val, cb)
                if lit is not None:
                    out[_node_text(nm, cb)] = lit
        for c in n.children:
            _walk(c)
    _walk(method_node)
    return out


# ---------- StringBuilder 链(按实例隔离 + §9.3 branch_detected) ----------

def _extract_string_builder(node, cb, source_path, results):
    if node.type != "method_declaration":
        for child in node.children:
            _extract_string_builder(child, cb, source_path, results)
        return
    if "append" not in _node_text(node, cb):
        return
    builder_vars = _find_builder_vars(node, cb)
    if not builder_vars:
        return
    for var_name in builder_vars:
        parts: list[str] = []
        branch_flag = [False]
        _collect_append_parts(node, cb, parts, var_name, branch_flag)
        if parts:
            sql = "".join(parts)
            if _looks_like_sql(sql):
                results.append(RecoveredSqlCandidate(
                    source_path=source_path,
                    line_start=node.start_point[0] + 1, line_end=node.end_point[0] + 1,
                    container=_find_container(node, cb), sql_text=sql,
                    recovery_mode="string_builder", confidence="low",
                    branch_detected=branch_flag[0]))


def _find_builder_vars(node, cb) -> list[str]:
    found: list[str] = []

    def _walk(n):
        if n.type == "local_variable_declaration":
            type_node = n.child_by_field_name("type")
            if type_node and _node_text(type_node, cb) in ("StringBuilder", "StringBuffer"):
                for child in n.children:
                    if child.type == "variable_declarator":
                        nm = child.child_by_field_name("name")
                        if nm:
                            found.append(_node_text(nm, cb))
        for c in n.children:
            _walk(c)
    _walk(node)
    return found


def _collect_append_parts(node, cb, parts, target_var, branch_flag):
    if node.type == "method_invocation":
        obj = node.child_by_field_name("object")
        method_name_node = node.child_by_field_name("name")
        if obj and method_name_node and _node_text(method_name_node, cb) == "append":
            obj_text = _node_text(obj, cb)
            if obj_text == target_var or obj_text.startswith(target_var + "."):
                if _has_branch_ancestor(node):
                    branch_flag[0] = True
                args = node.child_by_field_name("arguments")
                if args:
                    for arg in args.children:
                        if arg.type == "string_literal":
                            lit = _string_literal_value(arg, cb)
                            if lit is not None:
                                parts.append(lit)
                        elif arg.type not in (",", "(", ")"):
                            parts.append("${?}")
    for child in node.children:
        _collect_append_parts(child, cb, parts, target_var, branch_flag)


def _has_branch_ancestor(node) -> bool:
    """node 是否嵌套在 if/else/switch/for/while/ternary 内(§9.3)。"""
    current = node.parent
    while current and current.type != "method_declaration":
        if current.type in _BRANCH_NODE_TYPES:
            return True
        current = current.parent
    return False


# ---------- 公共 helper(移植 LP) ----------

def _looks_like_sql(text: str) -> bool:
    upper = text.upper().strip()
    return any(upper.startswith(kw) or f" {kw} " in upper
               for kw in ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "WITH"))


def _node_text(node, cb) -> str:
    return cb[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_container(node, cb) -> str:
    method_name = class_name = ""
    current = node
    while current:
        if current.type == "method_declaration" and not method_name:
            nm = current.child_by_field_name("name")
            if nm:
                method_name = _node_text(nm, cb)
        elif current.type == "class_declaration" and not class_name:
            nm = current.child_by_field_name("name")
            if nm:
                class_name = _node_text(nm, cb)
        current = current.parent
    if class_name and method_name:
        return f"{class_name}.{method_name}"
    return class_name or method_name or ""


def _find_enclosing_method(node):
    current = node.parent
    while current:
        if current.type == "method_declaration":
            return current
        current = current.parent
    return None


def _var_reaches_sink(var_name, method_node, cb) -> bool:
    def _search(n):
        if n.type == "method_invocation":
            nm = n.child_by_field_name("name")
            if nm and _node_text(nm, cb) in _SINK_METHODS:
                args = n.child_by_field_name("arguments")
                if args and var_name in _node_text(args, cb):
                    return True
        return any(_search(c) for c in n.children)
    return _search(method_node)
