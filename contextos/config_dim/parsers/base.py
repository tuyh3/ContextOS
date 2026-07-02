"""parser 协议 + 输出 dataclass + 按扩展名/类型注册表。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class ParsedItem:
    entity_key: str
    config_key: str
    key_path: str
    value_raw: str
    value_type: str = "string"
    default_value: str = ""
    scope: str = ""


@dataclass
class ParsedConfig:
    source_type: str          # 'file'
    file_path: str
    file_type: str            # properties/yaml/json/xml-spring/xml-mybatis
    framework: str = ""
    items: list[ParsedItem] = field(default_factory=list)
    class_refs: list[dict] = field(default_factory=list)   # spring xml: {bean_id, class_fqn, line}
    sql_refs: list[dict] = field(default_factory=list)     # mybatis: {namespace, stmt_id, sql_kind, line}


# Parser 签名: (path:str, text:str) -> ParsedConfig
_REGISTRY: dict[str, Callable[[str, str], ParsedConfig]] = {}


def register(*exts: str):
    def deco(fn):
        for e in exts:
            _REGISTRY[e] = fn
        return fn
    return deco


def parser_for(path: str):
    return _REGISTRY.get(Path(path).suffix.lower())


def infer_value_type(v: str) -> str:
    s = (v or "").strip()
    if s.lower() in ("true", "false"):
        return "bool"
    if s.lstrip("-").isdigit():
        return "int"
    return "string"
