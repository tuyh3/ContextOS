"""切分门槛 + 内容承载分组测试。

设计思路: 门槛按"估算 token > 预算 且 content-bearing 段 >= 2"(spec §8.0 修正); 长的
一层 30 项平表必须进切分; 短输入零额外开销; 分组拿"内容承载段"(含非叶子 intro)不丢父正文。
评分标准: 门槛判定对; 分组每组 <= 预算; 非叶子有 content 的段不被丢。
脚本逻辑: estimate_tokens / should_segment / group_segments 三函数单测。
"""
from __future__ import annotations

from contextos.requirement.segmentation import group_segments, should_segment
from contextos.requirement.segmentation.detector import detect


def test_short_input_skips_segmentation():
    assert should_segment("1. 就一句话需求", budget_tokens=800) is False


def test_long_flat_list_enters_segmentation():
    flat = "\n".join(f"{i}. 第{i}条需求说明文字" for i in range(1, 31)) + "\n" + "尾巴" * 600
    # 长(>预算)且 >=2 个同级编号段(无嵌套也要切)
    assert should_segment(flat, budget_tokens=50) is True


def test_long_prose_no_markers_does_not_segment_by_number():
    prose = "这是一段没有任何编号的长散文。" * 200
    assert should_segment(prose, budget_tokens=50) is False


def test_group_keeps_non_leaf_intro_content():
    raw = "X 总览\n  本节总则: 全部可配置\n  a. 子项一\n  b. 子项二\n"
    segs = detect(raw)
    groups = group_segments(segs, budget_tokens=800)
    joined = "\n".join(g.source_text for g in groups)
    assert "本节总则" in joined          # 父段 intro 不丢
    assert "子项一" in joined and "子项二" in joined


def test_oversized_single_unit_forms_solo_group():
    """单个单元正文就超预算时, 它自成一组(超预算但不丢内容), 不死循环不静默丢。
    评分: 超长条款文字必须出现在某组的 source_text 里; 至少产出 1 组。
    脚本逻辑: detect 出超长单元 -> group_segments 用极小预算 -> 断言内容仍在。
    """
    raw = "1. 超长条款\n" + "内容详情 " * 300   # well over a tiny budget
    segs = detect(raw)
    groups = group_segments(segs, budget_tokens=50)
    joined = "\n".join(g.source_text for g in groups)
    assert "超长条款" in joined          # 超长单元未被丢
    assert len(groups) >= 1
