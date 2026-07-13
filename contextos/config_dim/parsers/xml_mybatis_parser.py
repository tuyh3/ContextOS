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
from contextos.util.mybatis_sniff import sniff_mybatis_mapper_text, xml_localname as _localname

_STMT_KINDS = {"select", "insert", "update", "delete"}


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
    """dispatcher: 按根标签 sniff -> spring(beans) / mybatis(mapper) / 空。

    mapper 判定走 util.mybatis_sniff 共用实现(spec 附录 E.4 MUST: 与 lineage
    is_mybatis_mapper 同一口径, 防"什么算 mapper"两处漂移)。含一处行为收敛:
    解析失败但带 mybatis-3-mapper DTD 声明的文件现按 xml-mybatis 归类
    (身份不因当下解析不动而否决), 此前落 generic xml。
    """
    if sniff_mybatis_mapper_text(text):
        return parse_mybatis_xml(path, text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ParsedConfig(source_type="file", file_path=path, file_type="xml")
    if _localname(root.tag) == "beans":
        # spring parser lives in Task A8; import lazily so this module imports
        # cleanly even before A8 is merged (the mapper route is self-contained).
        from contextos.config_dim.parsers.xml_spring_parser import parse_spring_xml
        return parse_spring_xml(path, text)
    return ParsedConfig(source_type="file", file_path=path, file_type="xml")
