"""Query 翻译扩展(design 02 §3.4)。v1 只做静态 glossary(动态/字典 glossary defer)。

为 03 双路检索产 queries.zh + queries.en。glossary 命中术语指示 LLM 不翻译。
"""
from __future__ import annotations

from typing import Literal

from contextos.llm import LLMProvider
from contextos.requirement.schema import Queries, _StrictBase

# 静态 glossary:业务/FPA 术语不翻译(design §3.4 列举 + 常见 BSS 术语)
STATIC_GLOSSARY: frozenset[str] = frozenset({
    "USSD", "Bundle", "Advance Loan", "IFPUG",
    "EI", "EO", "EQ", "ILF", "EIF", "FTR", "DET", "RET",
    "SMS", "ESB", "FTTH", "CRM", "BSS", "OCS", "Dost",
})

_SYSTEM = (
    "你是双语 query 生成助手,为代码检索准备中英文检索 query。"
    "保持业务语义,产出简洁可检索的 query。只输出 JSON {\"zh\":..., \"en\":...}。"
)

_PROMPT_TMPL = (
    "源语言: {lang}\n"
    "业务意图: {intent}\n"
    "关键实体: {entities}\n"
    "{glossary_line}"
    "请产出中英文双语检索 query(zh + en)。"
)


class TranslationResult(_StrictBase):
    zh: str
    en: str


def detect_language(text: str) -> Literal["zh", "en", "mixed"]:
    """按 CJK 占比判语言:>0.6 zh / <0.1 en / 之间 mixed。无字母默认 en。"""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    total = cjk + latin
    if total == 0:
        return "en"
    ratio = cjk / total
    if ratio > 0.6:
        return "zh"
    if ratio < 0.1:
        return "en"
    return "mixed"


def _glossary_hits(text: str, glossary: frozenset[str]) -> list[str]:
    low = text.lower()
    return sorted(t for t in glossary if t.lower() in low)


def translate(
    llm: LLMProvider,
    business_intent: str,
    key_entities: list[str],
    *,
    glossary: frozenset[str] = STATIC_GLOSSARY,
) -> Queries:
    """business_intent 空 -> 空 Queries(不调 LLM)。LLMStructuredError 向上抛。"""
    if not business_intent.strip():
        return Queries()
    seed = business_intent + " " + " ".join(key_entities)
    lang = detect_language(seed)
    hits = _glossary_hits(seed, glossary)
    glossary_line = (
        f"以下术语保持原文不翻译: {', '.join(hits)}\n" if hits else ""
    )
    prompt = _PROMPT_TMPL.format(
        lang=lang,
        intent=business_intent,
        entities=", ".join(key_entities),
        glossary_line=glossary_line,
    )
    result = llm.structured(prompt, TranslationResult, system=_SYSTEM)
    return Queries(zh=result.zh, en=result.en)
