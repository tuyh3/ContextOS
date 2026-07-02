"""07 LLM 重排逐候选票 schema + 配置(对齐 07 design §4.2/§4.3/§6)。

RerankBatchOutput 是 LLM 一次调用(一个 chunk)的 structured() 产出;batch=1 时
votes 长度为 1。逐候选 vote 最终落进 ProviderCandidate.signals(见 provider.py)。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

WORKER_NAME = "llm_rerank"

Vote = Literal["support", "oppose", "abstain"]      # 语义票(design §4.2)
Status = Literal["ok", "failed", "skipped"]          # 运行态(与 vote 正交)
Dimension = Literal["method", "sql", "config"]


class _StrictBase(BaseModel):
    model_config = {"extra": "forbid"}


class RerankVoteItem(_StrictBase):
    """LLM 对 chunk 内单个候选的判读;candidate_index 对应 prompt 里的候选序号(0-based)。"""

    candidate_index: int
    vote: Vote
    relevance: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence_strength: Annotated[float, Field(ge=0.0, le=1.0)]
    reasoning: str = ""


class RerankBatchOutput(_StrictBase):
    """一次 LLM 调用的产出(一个 chunk 的全部票)。"""

    votes: list[RerankVoteItem]


class RerankConfig(_StrictBase):
    # ge=1 防 batch_size<=0(_chunks 已 max(1,n) 兜底, 此处再拦); cap ge=0 防负 cap 变 items[:-N] drop-last 怪语义
    # 每次 LLM 调用判几个候选。默认 8(不是 1): 逐候选(=1)时 80 候选(默认 caps 30+30+20)要打 80 次串行
    # LLM, 接真 DeepSeek 实测经常超时(2026-06-09 用户反馈)。批量 8 -> ~11 次往返, 约 7x 提速; 代价是
    # 一次判 8 个、一次坏响应丢这 8 个(降级 failed 非崩溃, _vote_chunk 已 fail-safe)。质量/速度旋钮可调。
    batch_size: Annotated[int, Field(ge=1)] = 8
    # chunk 间相互独立(各判各的候选, 无共享态)-> 并发跑这些阻塞型 DeepSeek 调用。默认 6: 11 个 chunk
    # 并发 6 约 2 波 ~30-45s(串行 ~2.8min)。太高撞 DeepSeek 限流(429->重试反更慢), 故不设很大。
    # =1 退回串行(确定性, 测试/小输入用)。线程池: llm.structured 是阻塞 I/O, openai SDK client 线程安全。
    max_concurrency: Annotated[int, Field(ge=1)] = 6
    method_cap: Annotated[int, Field(ge=0)] = 30  # 每维 defensive cap(design §6 级联 top-N 兜底)
    sql_cap: Annotated[int, Field(ge=0)] = 30
    config_cap: Annotated[int, Field(ge=0)] = 20
    rag_summary_max_chars: Annotated[int, Field(ge=0)] = 1200   # RAG 业务摘要总上限(约 < 500 token)
