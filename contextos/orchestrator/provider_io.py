"""08 数据流编排消费的统一 provider 输出契约(U0 共享接缝)。

对齐 08 §2「证据提供器 = 插件」统一 schema:所有 provider(04 code_search /
03 rag / 05 db_lineage_bridge / 06 config_dimension_bridge / 07 llm_rerank)
吐同一个信封,08 跑所有已注册 provider、不关心具体有哪些。

信封字段以 08 §2 为 SSOT —— 注意含 `reasoning`(04 §7 / 03 §10 各自的 design
草案漏了它,这里按 08 对齐补上,03 plan 直接复用本文件)。

candidate 的 provider 专属信号走开放 `signals: dict`(08 保持 provider-agnostic);
每个 provider 在自己模块里用强类型模型(如 04 的 CodeSearchSignals)校验后 dump
进 signals。kind 对齐 01 §3.1 Kind 开放枚举(contextos/impact_map/enums.py)。
"""
from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import BaseModel, Field


def _safe_float(value: Any, default: float = 0.0) -> float:
    """把任意 signal 值 coerce 成有限 float;非数值 / None / NaN / inf -> default。

    08 fail-safe(design §5.1):score_bridge 在逐候选 loop 里跑,单条坏候选(signal 值类型
    异常)不该崩整轮编排。U0 typed-signal 契约下正常不可达(各 provider 用强类型模型校验后才
    dump 进开放 signals dict),这里是 orchestrator 融合层的 defense-in-depth(亦护 Plan 10
    MCP host 输入路径)。NaN/inf 视作垃圾 -> default(0),不奖励满分。
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _safe_int(value: Any, default: int = 0) -> int:
    """把任意 signal 值 coerce 成 int;非整数 / None / NaN / inf / 溢出 -> default(同 _safe_float)。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


class _StrictBase(BaseModel):
    model_config = {"extra": "forbid"}


class ProviderCandidate(_StrictBase):
    """单条候选证据。08 corroboration 按 target 跨桥关联。"""

    target: str                       # 代码 FQN / 文档路径 / 表名 ...
    kind: str                         # 对齐 01 §3.1 Kind 开放枚举
    signals: dict[str, Any] = Field(default_factory=dict)  # provider 专属信号


class ProviderResult(_StrictBase):
    """所有证据提供器的统一输出信封(08 §2 SSOT)。

    字段 + 顺序 + 语义严格对齐 08 §2,不增不减:
    worker_name / score / score_breakdown / candidates / reasoning / miss_reason。
    """

    # 开放 str,**不可**收成 Literal 闭枚举:08 §2 架构 = 加 provider 只往注册表
    # 加、框架 0 改动(git_evidence 是 v2 才加)。闭枚举会违背 SSOT 扩展性意图。
    worker_name: str
    score: Annotated[float, Field(ge=0.0, le=1.0)]
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    candidates: list[ProviderCandidate] = Field(default_factory=list)
    reasoning: str = ""
    miss_reason: str | None = None

    @classmethod
    def miss(cls, worker_name: str, reason: str) -> "ProviderResult":
        """失败传播 helper(08 §5.1):provider miss -> score=0 + 空候选 + miss_reason。"""
        return cls(worker_name=worker_name, score=0.0, candidates=[], miss_reason=reason)
