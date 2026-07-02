from __future__ import annotations

import pytest
from pydantic import ValidationError

from contextos.rerank.schema import (
    WORKER_NAME, RerankBatchOutput, RerankConfig, RerankVoteItem,
)


def test_worker_name():
    assert WORKER_NAME == "llm_rerank"


def test_vote_item_valid():
    v = RerankVoteItem(candidate_index=0, vote="support", relevance=0.9,
                       evidence_strength=0.8, reasoning="主实现匹配")
    assert v.vote == "support" and v.candidate_index == 0


def test_vote_item_rejects_bad_vote():
    with pytest.raises(ValidationError):
        # model_validate 收 dict, 故意非法值不触发 pyright reportArgumentType(对齐 lineage 约定)
        RerankVoteItem.model_validate(
            {"candidate_index": 0, "vote": "maybe", "relevance": 0.5, "evidence_strength": 0.5})


def test_vote_item_clamps_range():
    with pytest.raises(ValidationError):
        RerankVoteItem(candidate_index=0, vote="support", relevance=1.5, evidence_strength=0.5)


def test_batch_output_is_list():
    out = RerankBatchOutput(votes=[
        RerankVoteItem(candidate_index=0, vote="oppose", relevance=0.1, evidence_strength=0.2),
    ])
    assert len(out.votes) == 1


def test_config_defaults():
    c = RerankConfig()
    assert c.batch_size == 8   # 2026-06-09: 1 -> 8 防真 DeepSeek 逐候选串行 80 次超时(见 provider 注释)
    assert c.max_concurrency == 6   # 2026-06-09: chunk 并发上限(线程池), 串行 2.8min -> 并发 ~30-45s
    assert (c.method_cap, c.sql_cap, c.config_cap) == (30, 30, 20)
    assert c.rag_summary_max_chars == 1200


def test_strict_forbids_extra():
    with pytest.raises(ValidationError):
        # model_validate 收 dict, 故意多余字段不触发 pyright reportCallIssue(对齐 lineage 约定)
        RerankConfig.model_validate({"unknown_field": 1})
