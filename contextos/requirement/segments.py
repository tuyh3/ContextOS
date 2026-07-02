"""需求切分统一中间表示(IR)。text/email(Path X)与 docx(增量2)两条进料口都归一到
list[Segment]; 生产者不同, IR 统一。字段说明见 spec §5。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Segment:
    segment_id: str            # 稳定 id(评测/去重/引用锚)
    parent_id: str | None      # 父段 id(None=根直属); parent 准度评测直接比这个
    ordinal: int               # 同级序号(successor 重建 + 稳定排序)
    level: int                 # 0=虚拟根; 标记从 1 起
    label: str                 # 短标号 "2"/"A"/"I"/"a"; 纯标题段可空
    marker_style: str          # arabic/upper/lower/roman/none
    title_path: list[str]      # 祖先标题链(context 注入 + 溯源; 非 source_span 来源)
    heading: str               # 本段标题行逐字原文
    content: str               # 本段正文(标记行后到下一标记行前; 可空)
    start: int                 # raw_text 字符起点
    end: int                   # raw_text 字符终点
    producer: str              # 本增量恒 "x"
    confidence: str            # high/low
    confidence_reason: str = ""  # ""|fallback-branch|bullet-ambiguous|big-gap
