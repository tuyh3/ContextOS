"""输入适配器:任意来源 -> 归一化纯文本。注册表 + 分发(design 02 §1.1)。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

PARSE_FAIL_PREFIX = "输入解析失败: "


@dataclass
class AdapterResult:
    """适配器输出:归一化纯文本 + 解析期开放问题。"""
    raw_text: str
    open_questions: list[str] = field(default_factory=list)


def parse_failure(reason: str) -> AdapterResult:
    """统一的失败返回(空 raw_text + 标准 open_question,不抛异常)。"""
    return AdapterResult(
        raw_text="",
        open_questions=[f"{PARSE_FAIL_PREFIX}{reason}, 需人工提供纯文本"],
    )


Adapter = Callable[[str], AdapterResult]

_REGISTRY: dict[str, Adapter] = {}


def register(source_kind: str, fn: Adapter) -> None:
    _REGISTRY[source_kind] = fn


def get_adapter(source_kind: str, *, profile=None) -> Adapter:
    """按 source_kind 取适配器。profile 非空时检查 input.adapters 开关。"""
    if profile is not None:
        adapters_cfg = getattr(getattr(profile, "input", None), "adapters", {})
        if adapters_cfg.get(source_kind) is False:
            raise ValueError(f"source_kind {source_kind!r} disabled in profile.input.adapters")
    fn = _REGISTRY.get(source_kind)
    if fn is None:
        raise ValueError(f"unsupported source_kind: {source_kind!r}")
    return fn
