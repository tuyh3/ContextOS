"""桥1 code_search provider 入口(04 §7 输出 + 08 §5.1 失败传播)。

search_code(breakdown, searcher) -> ProviderResult:
  1. 02b rejected 的需求直接 miss(不空跑 JDT LS)
  2. 02 -> 04 归一(input_adapter)
  3. 无 search_terms -> miss
  4. workspaceSymbol 种子搜索(seeds.find_seeds)
  5. 无命中 -> miss;有命中 -> 组装 score / score_breakdown
  6. searcher 抛错(JDT LS 起不来 / 超时)-> 当 miss,不抛(失败传播)
"""
from __future__ import annotations

import logging

from contextos.code_intel.code_search.input_adapter import breakdown_to_query
from contextos.code_intel.code_search.seeds import SymbolSearcher, find_seeds
from contextos.code_intel.projection.searcher import ProjectionMissingError
from contextos.orchestrator.provider_io import ProviderResult
from contextos.requirement.schema import RequirementBreakdown

logger = logging.getLogger(__name__)

WORKER_NAME = "code_search"
_SOURCE_CONFIDENCE_JDT_LS = 0.9  # 08 §3.3 来源置信度:JDT LS = 0.9


def search_code(breakdown: RequirementBreakdown, searcher: SymbolSearcher) -> ProviderResult:
    if breakdown.assessment == "rejected":
        return ProviderResult.miss(WORKER_NAME, "requirement_rejected")

    query = breakdown_to_query(breakdown)
    if not query.search_terms:
        return ProviderResult.miss(WORKER_NAME, "no_search_terms")

    try:
        seeds = find_seeds(searcher, query.search_terms)
    except ProjectionMissingError as exc:        # 投影没 build: 专属诚实 miss(spec D3)
        return ProviderResult(
            worker_name=WORKER_NAME, score=0.0, candidates=[],
            miss_reason="code_projection_not_built", reasoning=str(exc))
    except Exception as exc:  # JDT LS 起不来 / 超时 -> 失败传播(08 §5.1)
        # 直接构造(非 miss() 后 mutate):走构造校验路径, reasoning 入契约(review)
        return ProviderResult(
            worker_name=WORKER_NAME,
            score=0.0,
            candidates=[],
            miss_reason="jdtls_error",
            reasoning=f"workspaceSymbol failed: {type(exc).__name__}: {exc}",
        )

    if not seeds:
        return ProviderResult.miss(WORKER_NAME, "no_symbol_match")

    # 04b freshness 注入(duck-typing: live JDT adapter 没有 freshness 方法, 不注入不崩)
    fresh = getattr(searcher, "freshness", None)
    if callable(fresh):
        try:
            fv = fresh()
            for c in seeds:
                c.signals.update(fv)
        except Exception as exc:  # LOW-1: freshness 失败只丢 freshness, 种子照常返回
            logger.debug("freshness injection skipped: %s: %s",
                         type(exc).__name__, exc)

    top_name_match = max(c.signals["name_match_strength"] for c in seeds)
    score = top_name_match * _SOURCE_CONFIDENCE_JDT_LS
    return ProviderResult(
        worker_name=WORKER_NAME,
        score=score,
        score_breakdown={
            "top_name_match": top_name_match,
            "source_confidence": _SOURCE_CONFIDENCE_JDT_LS,
            "num_seeds": float(len(seeds)),
        },
        candidates=seeds,
        reasoning=f"workspaceSymbol matched {len(seeds)} seed symbol(s) for "
                  f"{len(query.search_terms)} term(s)",
        miss_reason=None,
    )
