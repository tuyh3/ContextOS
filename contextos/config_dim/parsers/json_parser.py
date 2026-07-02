"""json 配置 parser: 复用 yaml 拍平 + 非配置 .json 黑名单判定。"""
from __future__ import annotations

import json
from pathlib import Path

from contextos.config_dim.parsers.base import (
    ParsedConfig,
    ParsedItem,
    infer_value_type,
    register,
)
from contextos.config_dim.parsers.yaml_parser import _flatten  # 复用拍平


def is_blacklisted(path: str, blacklist: list[str]) -> bool:
    name = Path(path).name
    return name in blacklist


@register(".json")
def parse_json(path: str, text: str) -> ParsedConfig:
    pc = ParsedConfig(source_type="file", file_path=path, file_type="json")
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return pc  # fail-safe
    flat: list[tuple[str, str]] = []
    _flatten(data, "", flat)
    for key_path, val in flat:
        pc.items.append(ParsedItem(
            entity_key=key_path,
            config_key=key_path.split(".")[-1].split("[")[0],
            key_path=key_path,
            value_raw=val,
            value_type=infer_value_type(val),
        ))
    return pc
