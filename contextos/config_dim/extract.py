"""tree-sitter-java 抽配置引用(@Value/@ConfigurationProperties/getProperty/自研框架注解)。
FQN 以 source_path 为主锚(MEDIUM 2): AST package + enclosing class 直接生成, 不靠 workspaceSymbol 名字。
节点类型基于 tree-sitter-java(Plan 05 已 vendored); 若版本差异致断言失败, 按 Plan 05 经验做小校准。"""
from __future__ import annotations

import re
from dataclasses import dataclass

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

_LANG = Language(tsjava.language())
_PARSER = Parser(_LANG)

_PLACEHOLDER = re.compile(r"^\$\{([^}:]+)(?::[^}]*)?\}$")
# 默认配置访问方法(profile 可扩, v1 内置)
_CONFIG_METHODS = {"getProperty", "getParam", "getString", "getProperties", "getConfig"}
# 内置注解 -> ref_type
_VALUE_ANNOS = {"Value"}
_PREFIX_ANNOS = {"ConfigurationProperties"}


def config_marker_terms(framework_annotations: list[str] | None = None) -> tuple[set[str], set[str]]:
    """返回 (注解名集合, 配置方法名集合) —— 即 extract_config_refs 认的全部信号源头。

    与上面 _VALUE_ANNOS / _PREFIX_ANNOS / _CONFIG_METHODS 同源: 改了那些这里自动跟上
    (sound-by-construction)。config_dim/pipeline.py 的 ripgrep 预筛据此建匹配模式, 保证预筛
    命中是 extract 会抽到引用的文件的超集 —— 绝不漏(漏了就少建配置绑定)。
    """
    annos = _VALUE_ANNOS | _PREFIX_ANNOS | set(framework_annotations or [])
    return annos, set(_CONFIG_METHODS)


@dataclass
class ConfigRef:
    key_norm: str
    ref_type: str          # annotation_value / annotation_prefix / method_arg / annotation
    source_path: str
    line: int
    class_fqn: str
    enclosing_method: str = ""
    snippet: str = ""
    confidence: str = "medium"


def normalize_key(raw: str) -> str:
    raw = (raw or "").strip().strip('"').strip("'")
    m = _PLACEHOLDER.match(raw)
    return m.group(1).strip() if m else raw


def _txt(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "ignore")


def _string_args(node, src: bytes) -> list[str]:
    """收集一个 annotation/method_invocation 节点下的 string_literal 文本。"""
    out: list[str] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "string_literal":
            out.append(_txt(n, src).strip('"').strip("'"))
            continue
        stack.extend(n.children)
    return out


def _anno_name(node, src: bytes) -> str:
    name_node = node.child_by_field_name("name")
    full = _txt(name_node, src) if name_node else ""
    return full.rsplit(".", 1)[-1]  # 去包前缀 @org...Configuration -> Configuration


def _package(root, src: bytes) -> str:
    for n in root.children:
        if n.type == "package_declaration":
            return _txt(n, src).replace("package", "").strip().rstrip(";").strip()
    return ""


def _enclosing_class_name(node, src: bytes) -> str:
    cur = node.parent
    while cur is not None:
        if cur.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            nm = cur.child_by_field_name("name")
            return _txt(nm, src) if nm else ""
        cur = cur.parent
    return ""


def extract_config_refs(source_path: str, text: str, framework_annotations: list[str] | None = None) -> list[ConfigRef]:
    fw = set(framework_annotations or [])
    src = text.encode("utf-8")
    tree = _PARSER.parse(src)
    root = tree.root_node
    pkg = _package(root, src)
    refs: list[ConfigRef] = []

    def fqn_for(node) -> str:
        cls = _enclosing_class_name(node, src)
        return f"{pkg}.{cls}" if pkg and cls else (cls or "")

    stack = [root]
    while stack:
        n = stack.pop()
        if n.type in ("annotation", "marker_annotation"):
            name = _anno_name(n, src)
            args = _string_args(n, src)
            if name in _VALUE_ANNOS and args:
                refs.append(ConfigRef(normalize_key(args[0]), "annotation_value", source_path,
                                      n.start_point[0] + 1, fqn_for(n), confidence="high"))
            elif name in _PREFIX_ANNOS and args:
                refs.append(ConfigRef(normalize_key(args[0]), "annotation_prefix", source_path,
                                      n.start_point[0] + 1, fqn_for(n), confidence="high"))
            elif name in fw and args:
                refs.append(ConfigRef(normalize_key(args[0]), "annotation", source_path,
                                      n.start_point[0] + 1, fqn_for(n), confidence="high"))
        elif n.type == "method_invocation":
            mname_node = n.child_by_field_name("name")
            mname = _txt(mname_node, src) if mname_node else ""
            if mname in _CONFIG_METHODS:
                args_node = n.child_by_field_name("arguments")
                lits = _string_args(args_node, src) if args_node else []
                if lits:
                    refs.append(ConfigRef(normalize_key(lits[0]), "method_arg", source_path,
                                          n.start_point[0] + 1, fqn_for(n), confidence="medium"))
        stack.extend(n.children)
    return refs
