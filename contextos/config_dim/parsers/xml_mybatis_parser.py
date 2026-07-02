"""MyBatis mapper XML parser + unified `.xml` dispatcher(按根标签 sniff)。

输出 `<mapper namespace>` + `<select/insert/update/delete id>` 进 `sql_refs`
(关联 05 SQL_TEMPLATE)。`.xml` dispatcher 按根标签路由:
  beans  -> spring (parse_spring_xml, Task A8)
  mapper -> mybatis (parse_mybatis_xml, 本文件)
  其它   -> 空 ParsedConfig(file_type="xml")
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from contextos.config_dim.parsers.base import ParsedConfig, register

_STMT_KINDS = {"select", "insert", "update", "delete"}


def _localname(tag: str) -> str:
    """Strip XML namespace prefix: '{ns}mapper' -> 'mapper'."""
    if tag and tag[0] == "{":
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_mybatis_xml(path: str, text: str) -> ParsedConfig:
    pc = ParsedConfig(source_type="file", file_path=path, file_type="xml-mybatis", framework="mybatis")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return pc
    if _localname(root.tag) != "mapper":
        return pc
    namespace = root.get("namespace") or ""
    for el in root:
        kind = _localname(el.tag)
        if kind in _STMT_KINDS:
            pc.sql_refs.append({
                "namespace": namespace,
                "stmt_id": el.get("id") or "",
                "sql_kind": kind,
                "line": 0,
            })
    return pc


@register(".xml")
def parse_xml(path: str, text: str) -> ParsedConfig:
    """dispatcher: 按根标签 sniff -> spring(beans) / mybatis(mapper) / 空。"""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ParsedConfig(source_type="file", file_path=path, file_type="xml")
    rn = _localname(root.tag)
    if rn == "beans":
        # spring parser lives in Task A8; import lazily so this module imports
        # cleanly even before A8 is merged (the mapper route is self-contained).
        from contextos.config_dim.parsers.xml_spring_parser import parse_spring_xml
        return parse_spring_xml(path, text)
    if rn == "mapper":
        return parse_mybatis_xml(path, text)
    return ParsedConfig(source_type="file", file_path=path, file_type="xml")
