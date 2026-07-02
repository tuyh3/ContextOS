"""query-时 passage 窗口: 围着命中行取有界文本段喂 reranker(MVP 不预切 chunk)。"""
from __future__ import annotations


def window_passage(lines: list[str], hit_lineno: int, radius: int) -> str:
    """lines = 文件行列表(无换行); hit_lineno = 1-based 命中行号; radius = 上下各取几行。"""
    if not lines:
        return ""
    idx = max(0, hit_lineno - 1)
    start = max(0, idx - radius)
    end = min(len(lines), idx + radius + 1)
    return "\n".join(lines[start:end])
