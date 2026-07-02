"""Segment IR 字段契约测试。

设计思路: Segment 是 text/email 与 docx 两条切分进料口的统一中间表示。本测试只锁
字段存在 + 默认值, 不锁算法(算法在 detector 测)。
评分标准: 必填字段缺省即失败; 可空字段(content/label)允许空串。
脚本逻辑: 直接构造一个 Segment, 断言字段可读 + 类型对。
"""
from __future__ import annotations

from contextos.requirement.segments import Segment


def test_segment_fields_present():
    s = Segment(
        segment_id="seg-abc-1", parent_id=None, ordinal=0, level=0, label="",
        marker_style="none", title_path=[], heading="", content="",
        start=0, end=10, producer="x", confidence="high", confidence_reason="",
    )
    assert s.segment_id == "seg-abc-1"
    assert s.parent_id is None
    assert s.title_path == []
    assert s.confidence == "high"


def test_segment_child_carries_parent_and_path():
    s = Segment(
        segment_id="seg-abc-2", parent_id="seg-abc-1", ordinal=1, level=2, label="A",
        marker_style="upper", title_path=["账务", "前台新界面"], heading="A 实施转移",
        content="正文若干", start=20, end=40, producer="x",
        confidence="low", confidence_reason="fallback-branch",
    )
    assert s.parent_id == "seg-abc-1"
    assert s.title_path == ["账务", "前台新界面"]
    assert s.marker_style == "upper"
    assert s.confidence_reason == "fallback-branch"
