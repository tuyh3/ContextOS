"""需求信号词表:parse / load(运行期 0 token)+ 一次性自动补译 build。

运行期:Plan 02b Guard 1 预筛读默认表(我们维护双语)+ 可选客户表,纯字符串
匹配,0 token。客户只填一种语言,build_signal_terms_cache() 一次性补另一种语言
(复用 translate.detect_language),缓存成机器格式;运行期读缓存仍 0 token。

约定(抄 .gitignore):只认行首 `#` 为注释;`C#` / `order#status` 在自己行内正常收。
"""
from __future__ import annotations

from pathlib import Path

from contextos.llm import LLMError, LLMProvider
from contextos.requirement.schema import _StrictBase
from contextos.requirement.translate import detect_language

# contextos/requirement/signal_terms.py -> parents[2] = 仓根
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TERMS_PATH = REPO_ROOT / "data" / "requirement_signal_terms" / "default.txt"


def parse_flat_terms(text: str) -> list[str]:
    """平铺文本 -> 词列表。行首 # 注释跳过, 空行跳过, 两侧去空白。

    .gitignore 约定: 只认行首 `#`, 故 `C#` / `order#status` 在自己行内正常保留。
    """
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def load_signal_terms(
    *,
    customer_path: str | None = None,
    default_path: str | None = None,
    cache_path: str | None = None,
) -> set[str]:
    """信号词全集(casefold 归一)。运行期纯字符串, 不调 LLM。

    优先级: cache_path 存在 -> 只读 cache(自动补译产物);否则默认表 + 客户表合并。
    """
    if cache_path:
        cp = Path(cache_path).expanduser()
        if cp.exists():
            return {t.casefold() for t in parse_flat_terms(cp.read_text(encoding="utf-8"))}

    terms: set[str] = set()
    dp = Path(default_path).expanduser() if default_path else DEFAULT_TERMS_PATH
    if dp.exists():
        terms |= {t.casefold() for t in parse_flat_terms(dp.read_text(encoding="utf-8"))}
    if customer_path:
        cup = Path(customer_path).expanduser()
        if cup.exists():
            terms |= {t.casefold() for t in parse_flat_terms(cup.read_text(encoding="utf-8"))}
    return terms


class _TermTranslation(_StrictBase):
    translated: str


_TERM_TR_SYSTEM = (
    "你是术语翻译助手。把给定的一个需求信号词翻译成目标语言, "
    "只回该词的对应译法, 不要解释。只输出 JSON。"
)


def _translate_term(llm: LLMProvider, term: str, target_lang: str) -> str:
    """单词级补译(build 期一次性用)。失败返回空串(跳过该词补译, 不让 build 崩)。"""
    lang_name = "英文" if target_lang == "en" else "中文"
    prompt = f"把信号词「{term}」翻译成{lang_name}, 输出 {{\"translated\": \"...\"}}。"
    try:
        out = llm.structured(prompt, _TermTranslation, system=_TERM_TR_SYSTEM)
        return out.translated.strip()
    except LLMError:
        return ""


def build_signal_terms_cache(
    llm: LLMProvider,
    *,
    customer_path: str,
    out_path: str,
    default_path: str | None = None,
) -> set[str]:
    """一次性(客户初始化 / 词表变更):默认双语表 + 客户单语表 -> 自动补译 -> 双语全集缓存。

    复用 translate.detect_language 判客户词语言, 补另一种语言。运行期不调用本函数;
    运行期只 load_signal_terms(cache_path=out_path) 读缓存, 0 token。
    """
    dp = Path(default_path).expanduser() if default_path else DEFAULT_TERMS_PATH
    full: set[str] = set()
    if dp.exists():
        full |= {t.casefold() for t in parse_flat_terms(dp.read_text(encoding="utf-8"))}

    cup = Path(customer_path).expanduser()
    customer_terms = (
        parse_flat_terms(cup.read_text(encoding="utf-8")) if cup.exists() else []
    )
    for term in customer_terms:
        full.add(term.casefold())
        lang = detect_language(term)
        if lang == "zh":
            tr = _translate_term(llm, term, "en")
        elif lang == "en":
            tr = _translate_term(llm, term, "zh")
        else:
            tr = ""   # mixed: 跳过补译
        if tr:
            full.add(tr.casefold())

    out = Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(sorted(full)) + "\n", encoding="utf-8")
    return full
