"""手测 07 rerank:真 LLM 对一组合成候选投票,打印逐候选 vote/score/reasoning。

用法(主仓,需 config/profile.toml + 仓根 .env 的 DEEPSEEK_API_KEY):
  uv run python scripts/smoke_rerank.py
默认 NullLookup(不需 RAG 物化目录);带 --rag <materialized_dir> 则真接 03 RAG。

注:候选用中性合成名(TESTDB/APP, 非真客户 schema/db),守 feedback_offline_test_neutral_fixtures。
"""
from __future__ import annotations

import argparse

from contextos.llm.factory import provider_from_profile
from contextos.orchestrator.provider_io import ProviderCandidate
from contextos.profile.loader import load_profile
from contextos.rerank import NullLookup, RagBusinessDocLookup, rerank
from contextos.requirement.schema import MatchedCapability, Queries, RequirementBreakdown


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rag", help="materialized_dir, 接真 03 RAG 取业务摘要")
    args = ap.parse_args()

    profile = load_profile()
    llm = provider_from_profile(profile)

    lookup = NullLookup()
    if args.rag:
        from contextos.recall.rag_provider import RagProvider
        from contextos.recall.reranker import make_reranker  # make_reranker(cfg) -> Reranker
        rag = RagProvider(args.rag, make_reranker(profile.rag), profile.rag)
        lookup = RagBusinessDocLookup(rag)

    bd = RequirementBreakdown(
        requirement_id="smoke", raw_text="新增动态计费批量操作, 完成后发 SMS 提醒",
        source_kind="text", business_intent="新增动态计费批量操作 + SMS 提醒",
        key_entities=["动态计费", "批量", "SMS"],
        matched_capabilities=[MatchedCapability(capability="billing-charging", confidence=0.9)],
        queries=Queries(zh="动态计费 批量 短信", en="dynamic charging bulk sms"),
    )
    # 候选全用明显合成名(守 feedback_offline_test_neutral_fixtures, 不掺任何真客户痕迹):
    candidates = [
        ProviderCandidate(target="BillingBatchService#start", kind="METHOD",
                          signals={"name_match_strength": 1.0, "capability_match": "billing-charging"}),
        ProviderCandidate(target="CommonLoggingUtil#log", kind="METHOD",
                          signals={"name_match_strength": 0.4}),
        ProviderCandidate(target="TESTDB.APP.PRODUCT_RULE", kind="SQL_TABLE",
                          signals={"relation_type": "INSERT_SELECT", "evidence_count": 3}),
        ProviderCandidate(target="product.feature.enabled", kind="CONFIG_KEY",
                          signals={"entity_key": "product.feature.enabled",
                                   "bind_strategy": "exact_match"}),
    ]
    res = rerank(bd, candidates, llm, lookup=lookup)
    print(f"\nprovider score={res.score}  breakdown={res.score_breakdown}")
    print(f"reasoning: {res.reasoning}\n")
    for c in res.candidates:
        s = c.signals
        print(f"  {c.target:42s} [{s['dimension_adapter_used']:6s}] "
              f"vote={s['vote']:8s} status={s['status']:7s} score={s['vote_score']}  "
              f"-- {s['reasoning']}")


if __name__ == "__main__":
    main()
