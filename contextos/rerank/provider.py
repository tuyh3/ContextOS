"""桥 5 llm_rerank provider(07 design §4 I/O + §4.3 vote_score + §6 失败处理)。

rerank(breakdown, candidates, llm, *, lookup, config) -> ProviderResult。
07 是 08 内部第二阶段 step:吃候选池(不是裸需求),逐候选软投票,套统一桥信封吐回。
折叠 / 级联裁剪 / corroboration 加权全留给 08(本模块不做)。
"""
from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from contextos.llm.base import LLMProvider
from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.prompts.rerank import RERANK_SYSTEM, build_rerank_prompt
from contextos.rerank.adapters import (
    dimension_for_kind,
    extract_prompt_signals,
    redact_credentials,
)
from contextos.rerank.enricher import BusinessDocLookup, NullLookup
from contextos.rerank.schema import Dimension, RerankBatchOutput, RerankConfig, WORKER_NAME


_REL_W = 0.5    # design §4.3 钉死: relevance / evidence_strength 等权(非可调旋钮)
_EV_W = 0.5


def _vote_score(vote: str, relevance: float, evidence_strength: float) -> float:
    """§4.3 钉死:support -> 正向相关性贡献(等权均值);oppose / abstain / failed / skipped -> 0。"""
    if vote == "support":
        return round(max(0.0, min(1.0, _REL_W * relevance + _EV_W * evidence_strength)), 4)
    return 0.0


def _primary_capability(breakdown) -> str:
    caps = sorted(breakdown.matched_capabilities, key=lambda c: c.confidence, reverse=True)
    return caps[0].capability if caps else ""


def _chunks(items: list, n: int) -> Iterator[list]:
    n = max(1, n)
    for i in range(0, len(items), n):
        yield items[i: i + n]


def _voted_candidate(c: ProviderCandidate, dim: str, v) -> ProviderCandidate:
    return ProviderCandidate(target=c.target, kind=c.kind, signals={
        "vote": v.vote, "status": "ok",
        "vote_score": _vote_score(v.vote, v.relevance, v.evidence_strength),
        "relevance": round(v.relevance, 4), "evidence_strength": round(v.evidence_strength, 4),
        "dimension_adapter_used": dim, "reasoning": v.reasoning, "miss_reason": None,
    })


def _failed_candidate(c: ProviderCandidate, dim: str) -> ProviderCandidate:
    return ProviderCandidate(target=c.target, kind=c.kind, signals={
        "vote": "abstain", "status": "failed", "vote_score": 0.0,
        "relevance": 0.0, "evidence_strength": 0.0,
        "dimension_adapter_used": dim, "reasoning": "", "miss_reason": "llm_call_failed",
    })


def _skipped_candidate(c: ProviderCandidate, dim: str, miss_reason: str) -> ProviderCandidate:
    """07 收到但没跑 LLM 的候选(被 cap 掉 / kind 不属三维):标 skipped 保留,不静默丢。"""
    return ProviderCandidate(target=c.target, kind=c.kind, signals={
        "vote": "abstain", "status": "skipped", "vote_score": 0.0,
        "relevance": 0.0, "evidence_strength": 0.0,
        "dimension_adapter_used": dim, "reasoning": "", "miss_reason": miss_reason,
    })


def _bounded_rag_summary(rag_parts: list[str], max_chars: int) -> str:
    """拼 RAG 摘要并截断:在预算内的最后一个换行处切, 保留完整条目(不喂半截 entry)。"""
    raw = "\n".join(rag_parts)
    if len(raw) <= max_chars:
        return raw
    clipped = raw[:max_chars]
    nl = clipped.rfind("\n")
    return clipped[: nl + 1] if nl >= 0 else clipped


def _vote_chunk(
    chunk: list[ProviderCandidate],
    dim: Dimension,
    intent: str,
    cap: str,
    llm: LLMProvider,
    lookup: BusinessDocLookup,
    breakdown,                              # 仅透传给 lookup(intent/cap 已在上层从它抽出)
    config: RerankConfig,
) -> list[ProviderCandidate]:
    lines, rag_parts = [], []
    for idx, c in enumerate(chunk):
        sig = extract_prompt_signals(c.signals, dim)   # 白名单:敏感原始值绝不进 prompt
        lines.append(f"[{idx}] target={c.target} kind={c.kind} signals={sig}")
        if dim in ("sql", "config"):
            try:                            # 富化是 best-effort 上下文: lookup 抛错绝不阻塞投票(§6 + Protocol 契约)
                summ = lookup.lookup(c, breakdown)
            except Exception:               # noqa: BLE001 -- 任意非 fail-safe lookup 实现降档到无摘要
                summ = ""
            if summ.strip():
                rag_parts.append(f"[{idx}] {summ.strip()}")
    # §7 07 层兜底: build_rerank_prompt 的 4 个输入按"是否系统扫出的数据"分两类:
    #   - matched_capability: 受控 Literal enum -> 安全, 不处理;
    #   - business_intent: 用户需求文本(LLM 本就该看的载荷)-> 不 redact(redact 会自毁分析);
    #   - candidates_block(target+signals 文本)/ rag_summary: 系统从客户 code/config/db/语料扫出
    #     的数据 -> 各过 redact_credentials。上游万一把凭据塞进 target(jdbc:...scott/tiger@db)也不进
    #     外部 LLM。候选 signals 另由白名单先护(上面);redact 只作用 prompt 文本, 输出 target 保留原值。
    rag_summary = redact_credentials(_bounded_rag_summary(rag_parts, config.rag_summary_max_chars))
    candidates_block = redact_credentials("\n".join(lines))
    prompt = build_rerank_prompt(dim, business_intent=intent, matched_capability=cap,
                                 candidates_block=candidates_block, rag_summary=rag_summary)
    try:
        # §6: 任意 LLM 调用失败(不止 LLMError 子类) -> 整 chunk status=failed, 不阻塞 08 pipeline。
        # 只裹外部调用 + 结果映射; _voted_candidate 等后处理在 try 外, 自身 bug 仍会响亮抛出。
        out = llm.structured(prompt, RerankBatchOutput, system=RERANK_SYSTEM)
        by_index = {v.candidate_index: v for v in out.votes}
    except Exception:                       # noqa: BLE001 -- LLMProvider 是公共 seam, 任意 host/客户实现都可能抛非 LLMError
        by_index = {}
    results = []
    for idx, c in enumerate(chunk):
        v = by_index.get(idx)
        results.append(_failed_candidate(c, dim) if v is None else _voted_candidate(c, dim, v))
    return results


def rerank(
    breakdown,
    candidates: list[ProviderCandidate],
    llm: LLMProvider,
    *,
    lookup: BusinessDocLookup | None = None,
    config: RerankConfig | None = None,
) -> ProviderResult:
    config = config or RerankConfig()
    lookup = lookup or NullLookup()
    if breakdown.assessment == "rejected":
        return ProviderResult.miss(WORKER_NAME, "requirement_rejected")
    if not candidates:
        return ProviderResult.miss(WORKER_NAME, "no_candidates")

    by_dim: dict[Dimension, list[ProviderCandidate]] = {"method": [], "sql": [], "config": []}
    skipped_unknown: list[ProviderCandidate] = []
    for c in candidates:
        dim = dimension_for_kind(c.kind)
        if dim is None:                       # kind 不属三维(v2 占位 / OTHER / 未知)-> 不投, 标 skipped
            skipped_unknown.append(c)
        else:
            by_dim[dim].append(c)
    caps = {"method": config.method_cap, "sql": config.sql_cap, "config": config.config_cap}

    intent = breakdown.business_intent
    cap = _primary_capability(breakdown)
    out: list[ProviderCandidate] = []
    dim_counts = {"method": 0, "sql": 0, "config": 0}
    # 收集"判 chunk"(LLM 工作)按维序成扁平 job 列; 各维 chunk 数 + over-cap skipped 另存 layout,
    # 以便并发跑完后按原嵌套顺序逐字重组(输出与串行版完全一致, 下游不感知并发)。
    jobs: list[tuple[Dimension, list[ProviderCandidate]]] = []
    layout: list[tuple[int, list[ProviderCandidate]]] = []   # 每维: (chunk 数, over-cap skipped 列)
    for dim, items in by_dim.items():
        judged = items[: caps[dim]]           # defensive 每维 cap(级联兜底):只 LLM 判 top-N
        dim_counts[dim] = len(judged)
        chunks = list(_chunks(judged, config.batch_size))
        for chunk in chunks:
            jobs.append((dim, chunk))
        over_cap = [_skipped_candidate(c, dim, "cap_skipped") for c in items[caps[dim]:]]
        layout.append((len(chunks), over_cap))

    # chunk 间相互独立(各判各的, 无共享可变态)-> 并发跑这些阻塞型 LLM 调用。ThreadPoolExecutor.map
    # 保提交序, 故结果按 jobs 顺序对齐、可原样重组。max_concurrency<=1 或 <=1 个 job -> 退回串行。
    # 每个 _vote_chunk 自身已 fail-safe(LLM 抛错 -> 整 chunk failed, 不外溢), 故线程内异常不毒化池。
    def _run(job: tuple[Dimension, list[ProviderCandidate]]) -> list[ProviderCandidate]:
        d, ch = job
        return _vote_chunk(ch, d, intent, cap, llm, lookup, breakdown, config)
    if config.max_concurrency > 1 and len(jobs) > 1:
        with ThreadPoolExecutor(max_workers=config.max_concurrency) as ex:
            job_results = list(ex.map(_run, jobs))
    else:
        job_results = [_run(j) for j in jobs]

    # 按维序重组: 每维先它的 chunk 结果(顺序)再它的 over-cap skipped; 最后 unknown skipped。
    ri = 0
    for n_chunks, over_cap in layout:
        for _ in range(n_chunks):
            out.extend(job_results[ri])
            ri += 1
        out.extend(over_cap)                  # 被 cap 掉的:标 skipped 保留(不静默丢, 可 audit)
    for c in skipped_unknown:
        out.append(_skipped_candidate(c, "unsupported", "unsupported_kind"))

    vc = {"votes_cast": 0, "votes_support": 0, "votes_oppose": 0, "votes_abstain": 0,
          "votes_failed": 0, "votes_skipped": 0}
    scores = []
    for rc in out:
        s = rc.signals
        vc["votes_cast"] += 1                  # 每个候选都计入(失败/跳过的 vote 默认 abstain)
        assert s["vote"] in ("support", "oppose", "abstain"), f"unexpected vote={s['vote']}"
        vc[f"votes_{s['vote']}"] += 1          # 按 vote 值计数, support+oppose+abstain = votes_cast
        if s["status"] == "failed":
            vc["votes_failed"] += 1            # status 轴(与 vote 正交), 从 votes_abstain 里可减出真 abstain
        elif s["status"] == "skipped":
            vc["votes_skipped"] += 1
        # 契约 §4.2: provider score = **逐候选 vote_score 均值**(含 failed/skipped 的 0), 仅作 run summary。
        # 不能只算 ok -- 否则 1 support + 9 failed/skipped 会算成 1.0 而非应有的 0.1。
        scores.append(s["vote_score"])
    breakdown_floats = {k: float(v) for k, v in vc.items()}
    # *_count = 该维 LLM 实判(post-cap)候选数, 不是该维输入总数(被 cap 掉的进 votes_skipped)。
    breakdown_floats.update({f"{d}_count": float(n) for d, n in dim_counts.items()})
    prov_score = round(sum(scores) / len(scores), 4) if scores else 0.0
    # 真 abstain = votes_abstain 扣掉 failed/skipped(它们也记 vote=abstain); reasoning 报真 abstain 不虚高。
    true_abstain = vc["votes_abstain"] - vc["votes_failed"] - vc["votes_skipped"]
    extra = ""
    if vc["votes_failed"] or vc["votes_skipped"]:
        extra = f" / failed={vc['votes_failed']} / skipped={vc['votes_skipped']}"
    return ProviderResult(
        worker_name=WORKER_NAME, score=prov_score, score_breakdown=breakdown_floats,
        candidates=out,
        reasoning=(f"reranked {len(out)} 候选 "
                   f"(support={vc['votes_support']} / oppose={vc['votes_oppose']} / "
                   f"abstain={true_abstain}{extra})"),
        miss_reason=None,
    )
