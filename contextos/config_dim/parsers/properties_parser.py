# contextos/config_dim/parsers/properties_parser.py
from __future__ import annotations

from contextos.config_dim.parsers.base import ParsedConfig, ParsedItem, infer_value_type, register


@register(".properties")
def parse_properties(path: str, text: str) -> ParsedConfig:
    pc = ParsedConfig(source_type="file", file_path=path, file_type="properties")
    # 处理续行: 行尾 '\' 拼下一行
    raw_lines = text.splitlines()
    merged: list[str] = []
    buf = ""
    for ln in raw_lines:
        if buf:
            ln = buf + ln.strip()
            buf = ""
        if ln.rstrip().endswith("\\"):
            buf = ln.rstrip()[:-1].rstrip()
            continue
        merged.append(ln)
    for ln in merged:
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        if "=" not in s and ":" not in s:
            continue
        sep = "=" if ("=" in s and (":" not in s or s.index("=") < s.index(":"))) else ":"
        key, _, val = s.partition(sep)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        pc.items.append(ParsedItem(
            entity_key=key, config_key=key, key_path=key,
            value_raw=val, value_type=infer_value_type(val),
        ))
    return pc
