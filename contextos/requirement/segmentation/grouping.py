"""切分门槛 + 内容承载分组(spec §8.0 / §8.1)。"""
from __future__ import annotations

from dataclasses import dataclass

from contextos.requirement.segments import Segment
from contextos.requirement.segmentation.detector import detect


def estimate_tokens(text: str) -> int:
    """廉价 token 估算(中英混 ~ chars/2.5)。不用裸字符数对 token 预算。"""
    return int(len(text) / 2.5)


def should_segment(raw_text: str, *, budget_tokens: int = 800) -> bool:
    """门槛: 估算 token > 预算 且 X 检出 content-bearing 段 >= 2(spec §8.0)。
    长的一层平表(>=2 段无嵌套)进切分; 短输入 / 长无结构 不进。
    """
    if estimate_tokens(raw_text) <= budget_tokens:
        return False
    segs = [s for s in detect(raw_text) if s.level > 0]
    return len(segs) >= 2


@dataclass
class ExtractGroup:
    context_path: str    # 祖先标题串(仅供理解, 不可作 source_span 来源)
    source_text: str     # 本组实际文本(唯一允许出 source_span 的来源)
    title_path: list[str]  # 代码赋给候选的 segment_path


def _unit_text(seg: Segment) -> str:
    if not seg.content:
        return seg.heading
    if not seg.heading:
        return seg.content          # 根的前导正文(无 heading)
    return f"{seg.heading}\n{seg.content}"


def group_segments(segs: list[Segment], *, budget_tokens: int = 800) -> list[ExtractGroup]:
    """提取单元 = 任何 heading/content 非空的段(含非叶子 intro/总则, 不只叶子)+ 根的前导正文。
    按文档序并组到 token 预算, 每组带祖先标题 context。同一 title_path 的相邻单元合并。
    """
    units = [s for s in segs
             if (s.level > 0 and (s.heading or s.content)) or (s.level == 0 and s.content)]
    groups: list[ExtractGroup] = []
    buf: list[Segment] = []
    buf_tokens = 0

    def flush() -> None:
        nonlocal buf, buf_tokens
        if not buf:
            return
        path = buf[0].title_path
        ctx = " > ".join(path) if path else ""
        body = "\n".join(_unit_text(u) for u in buf)
        groups.append(ExtractGroup(context_path=ctx, source_text=body, title_path=list(path)))
        buf, buf_tokens = [], 0

    for u in units:
        t = estimate_tokens(_unit_text(u))
        # 祖先路径不同 或 超预算 -> 起新组(比较 buf 首元素的 title_path: 整批同路径单元一起刷新, 严格相邻合并不跨组回填)
        if buf and (buf[0].title_path != u.title_path or buf_tokens + t > budget_tokens):
            flush()
        buf.append(u)
        buf_tokens += t
    flush()
    return groups
