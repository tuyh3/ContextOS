"""yaml/yml 配置 parser: 递归 dot-path 拍平 + list 索引 path。"""
from __future__ import annotations

import yaml  # pyyaml 已是依赖

from contextos.config_dim.parsers.base import (
    ParsedConfig,
    ParsedItem,
    infer_value_type,
    register,
)


def _flatten(node, prefix: str, out: list[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _flatten(v, f"{prefix}[{i}]", out)
    else:
        out.append((prefix, "" if node is None else str(node)))


@register(".yaml", ".yml")
def parse_yaml(path: str, text: str) -> ParsedConfig:
    pc = ParsedConfig(source_type="file", file_path=path, file_type="yaml")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return pc  # fail-safe: 解析失败返空, 不挂全局
    flat: list[tuple[str, str]] = []
    if data is not None:
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
