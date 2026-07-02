"""桥 2 检索 provider: sparse 召回 + reranker 打分 -> 08 §2 统一契约输出。

复用 contextos/orchestrator/provider_io 的 ProviderResult / ProviderCandidate(共享接缝,
其 docstring 已写明 03 plan 直接复用)。dense 路默认关(spec §3.3); 开关打开但无 dense
impl 时显式 NotImplementedError(实装归 Plan 03.5, gate 评测决定)。任何步骤失败
fail-safe -> ProviderResult.miss(08 §5.1)。
"""
from __future__ import annotations

import logging
from pathlib import Path

from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.recall.reranker.base import Reranker
from contextos.recall.schema import RagSignals
from contextos.recall.sparse import ripgrep_hits
from contextos.recall.windowing import window_passage

_log = logging.getLogger(__name__)

WORKER_NAME = "rag"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


class RagProvider:
    def __init__(self, materialized_dir: str | Path, reranker: Reranker, cfg: object) -> None:
        # .expanduser(): 写入侧 ops.paths.resolved_materialized_dir 已展开 ~, 检索侧
        # 必须同口径(spec Appendix C MUST 同一 resolver); 防 host 直接传字面 ~ 时
        # 写入/检索分叉 -> confirmed-cases prefix .exists() 假阴 -> strict scope 回退全量。
        self._dir = Path(materialized_dir).expanduser()
        self._reranker = reranker
        self._radius = int(getattr(cfg, "window_radius", 8))
        self._max_per_doc = int(getattr(cfg, "max_passages_per_doc", 3))
        self._dense_enabled = bool(getattr(cfg, "dense_enabled", False))

    def search(self, query: dict) -> ProviderResult:
        if self._dense_enabled:
            raise NotImplementedError(
                "dense 路尚未实装(MVP sparse-only); dense embedding 实装见 Plan 03.5(gate 决定)"
            )
        try:
            return self._search_sparse(query)
        except Exception as exc:  # fail-safe -> miss(08 §5.1)
            _log.warning("rag provider failed: %s", exc)
            return ProviderResult.miss(WORKER_NAME, f"provider_error:{type(exc).__name__}")

    def _search_sparse(self, query: dict) -> ProviderResult:
        if not self._dir.exists():
            return ProviderResult.miss(WORKER_NAME, "materialized_dir_missing")
        patterns = [t for t in query.get("key_entities", []) if str(t).strip()]
        if not patterns:
            return ProviderResult.miss(WORKER_NAME, "no_patterns")
        # path_prefixes(Plan 10 rag_search corpus scope)优先; 回退 matched_capabilities(08 编排域 hint)
        prefixes = ([str(p) for p in query.get("path_prefixes", [])]
                    or [str(c) for c in query.get("matched_capabilities", [])] or None)
        # domain hint -> 路径过滤: 仅当对应子目录存在时才用(否则全量搜)
        if prefixes:
            prefixes = [p for p in prefixes if (self._dir / p).exists()] or None

        hits = ripgrep_hits(patterns, self._dir, path_prefixes=prefixes)
        if not hits:
            return ProviderResult.miss(WORKER_NAME, "sparse_no_hits")

        # 每个 doc 取至多 max_per_doc 个命中 -> 窗口 passage
        by_doc: dict[str, list] = {}
        for h in hits:
            by_doc.setdefault(h.rel_path, []).append(h)

        rerank_query = (query.get("queries", {}).get("zh", "") + " "
                        + query.get("queries", {}).get("en", "")).strip() or " ".join(patterns)

        candidates: list[ProviderCandidate] = []
        for rel_path, doc_hits in by_doc.items():
            file_lines = (self._dir / rel_path).read_text(encoding="utf-8", errors="ignore").splitlines()
            picked = doc_hits[: self._max_per_doc]
            passages = [window_passage(file_lines, h.lineno, self._radius) for h in picked]
            scores = self._reranker.score(rerank_query, passages)
            best_i = max(range(len(scores)), key=lambda i: scores[i]) if scores else 0
            best_passage = passages[best_i] if passages else ""
            best_score = _clamp01(float(scores[best_i])) if scores else 0.0
            # evidence_origin 语义: "ocr" = 证据 passage 窗口内含 OCR 物化的截图文本
            # (`[image N OCR]` marker), 不保证命中行本身就是 OCR 行。精确到命中行的 OCR
            # 归属需 Plan 03a 物化层标 OCR 行范围(当前不产出), 故 MVP 用窗口级近似。
            # known-limitation: window_radius 较大时可能把邻近(非命中行)的截图文本算作来源。
            sig = RagSignals(
                rerank_score=round(best_score, 4),
                snippet=best_passage[:500],
                evidence_origin="ocr" if "[image" in best_passage else "text",
                lineno=picked[best_i].lineno if picked else -1,
                num_hits=len(doc_hits),
            )
            candidates.append(
                ProviderCandidate(target=rel_path, kind="BUSINESS_DOC", signals=sig.model_dump())
            )

        candidates.sort(key=lambda c: c.signals["rerank_score"], reverse=True)
        candidates = candidates[:10]
        top1 = candidates[0].signals["rerank_score"] if candidates else 0.0
        return ProviderResult(
            worker_name=WORKER_NAME,
            score=_clamp01(round(top1, 4)),
            score_breakdown={
                "top1_rerank": round(top1, 4),
                "coverage_domain": 1.0 if prefixes else 0.0,
                "sparse_path": 1.0,
                "dense_path": 0.0,
            },
            candidates=candidates,
            reasoning=f"sparse matched {len(by_doc)} doc(s) over {len(patterns)} pattern(s)",
            miss_reason=None,
        )
