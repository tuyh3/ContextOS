"""MyBatis mapper 内容 sniff —— "什么算 mapper" 的唯一判定实现(spec 附录 E.4)。

config_dim `xml_mybatis_parser`(.xml dispatcher)与 lineage `mybatis_extract`
(`is_mybatis_mapper`)共用本函数, 防两处口径漂移(spec E.4 MUST, 冷评审 N2)。
判定按**内容**不按目录约定(大写 Resources / src/main/java 下 XML 都认;
target/-bak 等目录排除是扫描层的职责, 不在本函数)。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

# mybatis-3-mapper DTD 声明标记(大小写无关比对)
_MYBATIS_DTD_MARKER = "mybatis-3-mapper.dtd"


def xml_localname(tag: str) -> str:
    """剥 XML namespace 前缀: '{ns}mapper' -> 'mapper'。"""
    if tag and tag[0] == "{":
        return tag.rsplit("}", 1)[-1]
    return tag


def sniff_mybatis_mapper_text(text: str) -> bool:
    """按内容判定一段 XML 文本是否 MyBatis mapper。

    主判定: XML 可解析且根标签为 mapper(带 xmlns 前缀也认)。
    兜底: 解析失败(未定义实体/编码噪声等)但含 mybatis-3-mapper DTD 声明
    仍认作 mapper —— 文件身份不因当下解析不动而否决(spec E.4:
    DTD 或根标签, 二者任一)。
    """
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        return _MYBATIS_DTD_MARKER in text.lower()
    return xml_localname(root.tag) == "mapper"
