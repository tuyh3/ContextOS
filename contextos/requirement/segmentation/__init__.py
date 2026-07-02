"""需求切分引擎。本增量 X-only; Y/reconcile 见 spec §6.2/§6.3(deferred)。"""
from contextos.requirement.segmentation.detector import detect
from contextos.requirement.segmentation.grouping import (
    ExtractGroup,
    estimate_tokens,
    group_segments,
    should_segment,
)


def segment(raw_text: str, *, profile=None):
    """raw_text -> list[Segment](本增量 = X detector; Y deferred)。"""
    return detect(raw_text)


__all__ = ["segment", "detect", "should_segment", "group_segments",
           "estimate_tokens", "ExtractGroup"]
