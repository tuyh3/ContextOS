"""Spring XML(<beans>)parser。

输出 `<bean id class>` 进 ParsedConfig.class_refs(供 bind_resolver Step 3 按 XML
id/ref 解析 java_class 绑定)+ `<property name value>` 进 items(config_key =
"<bean_id>.<property_name>")。

注: `.xml` 的扩展名注册由 A9 dispatcher 统一收口(按根标签 sniff spring vs
mybatis 再路由), 本 task 只实现 spring 解析函数, **不**加 @register。

XXE: stdlib `xml.etree.ElementTree` 默认不解析外部实体(Py3.8+ 安全), 无需
额外依赖。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from contextos.config_dim.parsers.base import (
    ParsedConfig,
    ParsedItem,
    infer_value_type,
)


def _localname(tag: str) -> str:
    """去掉 {namespace} 前缀, 返回本地标签名。"""
    return tag.rsplit("}", 1)[-1]


def parse_spring_xml(path: str, text: str) -> ParsedConfig:
    pc = ParsedConfig(
        source_type="file", file_path=path, file_type="xml-spring", framework="spring"
    )
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return pc
    if _localname(root.tag) != "beans":
        # 非 spring beans 根 -> 返回空(由 A9/A12 dispatcher 再路由)
        return ParsedConfig(source_type="file", file_path=path, file_type="xml-spring")
    for bean in root.iter():
        if _localname(bean.tag) != "bean":
            continue
        bean_id = bean.get("id") or bean.get("name") or ""
        cls = bean.get("class") or ""
        if cls:
            pc.class_refs.append({"bean_id": bean_id, "class_fqn": cls, "line": 0})
        for prop in bean:
            if _localname(prop.tag) != "property":
                continue
            pname = prop.get("name") or ""
            pval = prop.get("value")
            if pname and pval is not None:
                key = f"{bean_id}.{pname}" if bean_id else pname
                pc.items.append(
                    ParsedItem(
                        entity_key=key,
                        config_key=key,
                        key_path=key,
                        value_raw=pval,
                        value_type=infer_value_type(pval),
                    )
                )
    return pc
