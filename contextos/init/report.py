"""contextos init 的结构化汇总(spec §5.2)。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class _StrictBase(BaseModel):
    model_config = {"extra": "forbid"}   # 项目惯例: 拼错字段名(count vs counts)构建即报错, 不静默吞


class StepResult(_StrictBase):
    dimension: Literal["code", "database", "config", "corpus"]
    status: Literal["ok", "degraded", "skipped", "failed"]
    counts: dict[str, int] = {}
    detail: str = ""


class InitReport(_StrictBase):
    steps: list[StepResult]
    verdict: Literal["ready", "degraded", "aborted"]
    reasons: list[str] = []
