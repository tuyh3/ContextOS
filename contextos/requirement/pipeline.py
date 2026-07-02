"""02 需求拆解 pipeline 编排:三道 guard + adapter -> extract -> classify -> translate。

Guard 1(scope): 纯代码预筛 + LLM scope judge, 判"不是软件需求"早退省下游 3 次调用。
Guard 2(grounding): extract 后核验候选 source_span 是否在原文, 砍脑补。
Guard 3(uncertainty): confidence = scope_score x grounding_coverage(透明相乘, 不用模型自报数);
                      三档 assessment(rejected/degraded/ok)。
失败 fail-open: 任何 guard 自身失败一律往 DEGRADED 走, 绝不静默 REJECT 也不静默判 OK。
正则基线候选不依赖 LLM, 即使抽取降级仍产 candidate_code_names。
"""
from __future__ import annotations

import hashlib

from contextos.llm import LLMError, LLMProvider
from contextos.profile.schema import ScopeConfig
from contextos.requirement.adapters import get_adapter
from contextos.requirement.classifier import classify
from contextos.requirement.extract import _regex_baseline, extract
from contextos.requirement.grounding import coverage, ground_candidates
from contextos.requirement.schema import DictHits, Queries, RequirementBreakdown
from contextos.requirement.scope import prefilter, scope_judge
from contextos.requirement.segmentation import group_segments, segment, should_segment
from contextos.requirement.signal_terms import load_signal_terms
from contextos.requirement.translate import translate


def _make_id(raw_text: str, source_kind: str) -> str:
    h = hashlib.sha1(f"{source_kind}:{raw_text}".encode("utf-8")).hexdigest()[:8]
    return f"req-{h}"


def _dedup(cands: list):
    """跨段合并去重: 同 term(小写)留首个。泛化 extract._merge_code_names 到三维。"""
    seen, out = set(), []
    for c in cands:
        k = c.term.lower()
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _rejected(
    rid: str, raw_text: str, source_kind: str, open_questions: list[str]
) -> RequirementBreakdown:
    """REJECT 早退:不调下游 extract/classify/translate(省钱)。"""
    return RequirementBreakdown(
        requirement_id=rid,
        raw_text=raw_text,
        source_kind=source_kind,
        assessment="rejected",
        confidence=0.0,
        open_questions=open_questions,
    )


def breakdown(
    raw_input: str,
    source_kind: str,
    *,
    llm: LLMProvider,
    profile=None,
    requirement_id: str | None = None,
) -> RequirementBreakdown:
    """需求文本 -> RequirementBreakdown。raw_input 是 text 内容或 docx 路径。"""
    adapter = get_adapter(source_kind, profile=profile)
    adapted = adapter(raw_input)
    rid = requirement_id or _make_id(adapted.raw_text, source_kind)
    cfg: ScopeConfig = profile.input.scope if profile is not None else ScopeConfig()

    # 空文本 / 解析失败:短路(guard 之前), 不调 LLM -> REJECT
    if not adapted.raw_text.strip():
        return _rejected(
            rid, "", source_kind,
            adapted.open_questions or ["输入解析失败: 空文本, 需人工提供纯文本"],
        )

    raw_text = adapted.raw_text
    open_questions = list(adapted.open_questions)

    # --- Guard 1a: 预筛(纯代码, 0 token) ---
    if cfg.prefilter_enabled:
        terms = load_signal_terms(customer_path=cfg.signal_terms_path or None)
        if prefilter(raw_text, cfg, terms):
            return _rejected(
                rid, raw_text, source_kind,
                open_questions + ["预筛拦截: 明显非软件需求(短 + 真文字占比低 + 无需求信号词)"],
            )

    # --- Guard 1b: scope judge(LLM, 在 extract 之前 -> 早退省下游) ---
    verdict = scope_judge(llm, raw_text, cfg)
    if not verdict.failed and verdict.scope_score < cfg.reject_below:
        return _rejected(
            rid, raw_text, source_kind,
            open_questions
            + [f"scope 判定非软件需求(score={verdict.scope_score:.2f}), 早退省下游"],
        )

    forced_degraded = verdict.failed
    if verdict.failed:
        open_questions.append("scope 判定降级(LLM 失败 fail-open), 放行待人工确认")

    # --- 抽取(降级:正则基线仍兜底; 分段时逐组 try, 单组失败不拖垮其余, MEDIUM 4)---
    business_intent = ""
    key_entities: list = []
    actions: list = []
    code_names = _regex_baseline(raw_text, stop_keywords_path=cfg.stop_keywords_path or None)
    table_terms: list = []
    config_keys: list = []
    seg_low = False
    if should_segment(raw_text):
        segs = segment(raw_text)
        markers = [s for s in segs if s.level > 0]
        seg_low = sum(1 for s in markers if s.confidence == "low") > max(1, len(markers) // 2)
        groups = group_segments(segs)
        merged_code, merged_tbl, merged_cfg = [], [], []
        n_fail = 0
        for g in groups:
            try:
                ext = extract(
                    llm, g.source_text, context_path=g.context_path,
                    stop_keywords_path=cfg.stop_keywords_path or None,
                )
            except LLMError:
                n_fail += 1                               # 单组失败: 跳过, 不丢已成功组
                continue
            if not business_intent:
                business_intent = ext.business_intent
            key_entities.extend(ext.key_entities)
            actions.extend(ext.actions)
            for c in (*ext.candidate_code_names, *ext.candidate_table_terms,
                      *ext.candidate_config_keys):
                c.segment_path = list(g.title_path)        # 代码赋, 不让 LLM 自报
            merged_code.extend(ext.candidate_code_names)
            merged_tbl.extend(ext.candidate_table_terms)
            merged_cfg.extend(ext.candidate_config_keys)
        if n_fail == len(groups):                          # 全组失败 -> 退正则基线
            forced_degraded = True
            open_questions.append("LLM 抽取全组降级, 仅正则基线种子可用")
        else:
            if n_fail:
                forced_degraded = True
                open_questions.append(f"LLM 抽取 {n_fail}/{len(groups)} 组降级")
            code_names = _dedup(merged_code + code_names)
            table_terms = _dedup(merged_tbl)
            config_keys = _dedup(merged_cfg)
    else:
        try:
            ext = extract(llm, raw_text, stop_keywords_path=cfg.stop_keywords_path or None)
            business_intent = ext.business_intent
            key_entities = ext.key_entities
            actions = ext.actions
            code_names = ext.candidate_code_names   # 已含正则基线合并
            table_terms = ext.candidate_table_terms
            config_keys = ext.candidate_config_keys
        except LLMError as e:
            forced_degraded = True
            open_questions.append(f"LLM 抽取降级({type(e).__name__}), 仅正则基线种子可用")

    if seg_low:
        forced_degraded = True
        open_questions.append("本需求分段置信低, 可能漏召回")

    # --- 分类(降级:空能力)---
    capabilities: list = []
    try:
        capabilities = classify(llm, raw_text, business_intent=business_intent)
    except LLMError as e:
        forced_degraded = True
        open_questions.append(f"LLM 能力分类降级({type(e).__name__})")

    # --- 翻译(降级:空 query)---
    queries = Queries()
    try:
        queries = translate(llm, business_intent, key_entities)
    except LLMError as e:
        forced_degraded = True
        open_questions.append(f"LLM query 翻译降级({type(e).__name__})")

    # --- Guard 2: grounding(纯代码, 0 token)砍掉原文无出处的脑补候选 ---
    code_names, dropped_cn = ground_candidates(code_names, raw_text)
    table_terms, dropped_tt = ground_candidates(table_terms, raw_text)
    config_keys, dropped_ck = ground_candidates(config_keys, raw_text)
    kept = len(code_names) + len(table_terms) + len(config_keys)
    n_dropped = len(dropped_cn) + len(dropped_tt) + len(dropped_ck)
    grounding_coverage = coverage(kept, kept + n_dropped)
    if n_dropped:
        open_questions.append(f"grounding 砍掉 {n_dropped} 个原文无出处的脑补候选")

    # --- Guard 3: 置信聚合 + 定档 ---
    scope_score = 0.0 if verdict.failed else verdict.scope_score
    conf = scope_score * grounding_coverage
    if forced_degraded or conf < cfg.degraded_below:
        assessment = "degraded"
        # 既没 guard 失败也没砍候选, 却仍 DEGRADED -> 只剩"scope_score 处于
        # reject_below~degraded_below 边界"这一种原因; 补一条 open_question 交代清楚。
        if not forced_degraded and not n_dropped:
            open_questions.append(f"整体置信偏低(conf={conf:.2f}), 需人工确认")
    else:
        assessment = "ok"

    return RequirementBreakdown(
        requirement_id=rid,
        raw_text=raw_text,
        source_kind=source_kind,
        assessment=assessment,
        confidence=conf,
        business_intent=business_intent,
        key_entities=key_entities,
        actions=actions,
        matched_capabilities=capabilities,
        candidate_code_names=code_names,
        candidate_table_terms=table_terms,
        candidate_config_keys=config_keys,
        dict_hits=DictHits(),   # v1 deferred
        queries=queries,
        open_questions=open_questions,
    )
