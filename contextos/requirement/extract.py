"""LLM 辅助抽取(design 02 §3.1)。一次 structured() 产业务语义 + 三维候选,
正则基线(POC keyword_extract)兜底合并进 candidate_code_names。"""
from __future__ import annotations

import re
from itertools import chain

from contextos.llm import LLMProvider
from contextos.recall.keyword_extract import extract_keywords
from contextos.requirement.schema import (
    ActionKind,
    CandidateConfigKey,
    CandidateName,
    CandidateTableTerm,
    _StrictBase,
)

_SHOUTY_RE = re.compile(r"^[A-Z][A-Z_0-9]{2,}$")

_SYSTEM = (
    "你是需求分析助手。读一段电信 BSS 需求文本,抽出:业务意图、关键业务实体、"
    "动作分类(add/modify/delete),以及给代码/SQL表/配置三类搜索用的候选种子。"
    "候选种子要包含可能的英文类名/缩写(如 DynamicCharging / FTTH)和业务实体词。"
    "每个候选都要给 source_span(它在待抽取正文的出处片段),没出处就留空, 不要编造。"
    "只输出 JSON。"
    "source_span 必须来自'待抽取正文', 绝不引'上下文路径'。"
)

_CONTEXT_BLOCK = (
    "上下文路径(祖先标题, 仅供理解本段属于哪一节, 严禁作为 source_span 来源):\n{path}\n\n"
)

_PROMPT_TMPL = (
    "{context_block}"
    "待抽取正文:\n{raw_text}\n\n"
    "请抽取:\n"
    "- business_intent: 一句话业务意图\n"
    "- key_entities: 关键业务实体词列表\n"
    "- actions: 动作分类,取值仅限 add/modify/delete\n"
    "- candidate_code_names: 可能的代码名(term + kind[shouty/camelcase/proper_noun/other]"
    " + source 填 llm + source_span 填该候选在上面待抽取正文里的出处片段)\n"
    "- candidate_table_terms: 可能的SQL表/实体词(term + kind[entity/table_hint/business_term]"
    " + source 填 llm + source_span 填原文出处片段)\n"
    "- candidate_config_keys: 可能的配置key/参数词(term + kind[config_key/param_term/config_table_hint]"
    " + source 填 llm + source_span 填原文出处片段)\n"
    "重要: source_span 必须是待抽取正文里真实出现的片段, 不要编造; 实在没有出处就留空字符串。\n"
)


class ExtractionResult(_StrictBase):
    business_intent: str
    key_entities: list[str]
    actions: list[ActionKind]
    candidate_code_names: list[CandidateName]
    candidate_table_terms: list[CandidateTableTerm]
    candidate_config_keys: list[CandidateConfigKey]


def _regex_baseline(
    raw_text: str, stop_keywords_path: str | None = None
) -> list[CandidateName]:
    out: list[CandidateName] = []
    for kw in extract_keywords(raw_text, customer_stop_path=stop_keywords_path):
        kind = "shouty" if _SHOUTY_RE.match(kw) else "camelcase"
        out.append(CandidateName(term=kw, kind=kind, source="regex", source_span=kw))
    return out


def _merge_code_names(
    llm_names: list[CandidateName], regex_names: list[CandidateName]
) -> list[CandidateName]:
    seen = {c.term.lower() for c in llm_names}
    merged = list(llm_names)
    for c in regex_names:
        if c.term.lower() not in seen:
            merged.append(c)
            seen.add(c.term.lower())
    return merged


def extract(
    llm: LLMProvider,
    raw_text: str,
    *,
    context_path: str = "",
    stop_keywords_path: str | None = None,
) -> ExtractionResult:
    """LLM 抽取 + 正则基线合并。context_path 仅供理解(不可出 source_span); source_span
    只能来自 raw_text(待抽取正文)。LLMStructuredError 向上抛(pipeline 兜底)。
    """
    context_block = _CONTEXT_BLOCK.format(path=context_path) if context_path else ""
    result = llm.structured(
        _PROMPT_TMPL.format(context_block=context_block, raw_text=raw_text),
        ExtractionResult,
        system=_SYSTEM,
    )
    # LLM 产的候选 source 一律标 llm, 三维都盖(不信任自填值; grounding 对
    # source=regex 豁免 source_span 核验, 放任自称 regex 即绕过 Guard 2)
    for c in chain(
        result.candidate_code_names,
        result.candidate_table_terms,
        result.candidate_config_keys,
    ):
        c.source = "llm"
    # 合并正则基线(source=regex),去重 LLM 优先
    result.candidate_code_names = _merge_code_names(
        result.candidate_code_names, _regex_baseline(raw_text, stop_keywords_path)
    )
    return result
