"""Keyword extraction for ContextOS predict pipeline.

Extracts predict-friendly keywords from a requirement docx / requirement_summary:

  1. SHOUTY_CASE acronyms — e.g. FTTH, CPN, AAA_STATUS
  2. CamelCase phrases   — multi-word Title-Case sequences in the docx (e.g.
     "Change User Package") are glued into CamelCase keywords (ChangeUserPackage)
     so they match Java class names like `ChangeUserPackage.java`.

Filters out overly-broad keywords. Universal stop-list ships at
`data/stop_keywords/default.json`; an optional customer-specific list merges in
via `load_stop_list(customer_path=...)` (see profile.input.scope.stop_keywords_path).

The output is a sorted unique list of keyword strings consumed by
`requirement/extract.py:_regex_baseline` (regex baseline merged into
candidate_code_names).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STOP_LIST = REPO_ROOT / "data" / "stop_keywords" / "default.json"

# SHOUTY_CASE: 3+ char tokens of [A-Z][A-Z_0-9]+, word-bounded
_SHOUTY_RE = re.compile(r"\b[A-Z][A-Z_0-9]{2,}\b")


def _parse_customer_stop(text: str) -> list[str]:
    """客户扁平停用词: 行首 # 注释跳过, 空行跳过, 每行取第一个空白分隔字段, 归一大写。
    故草稿的 `TOKEN <df>` / `TOKEN  # note` 核对后可直接用, 无需删 df 标注。"""
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s.split()[0].upper())
    return out


def _flatten_default(data: dict) -> set[str]:
    out: set[str] = set()
    for cat in (data.get("categories") or {}).values():
        for kw in cat.get("keywords") or []:
            out.add(kw.upper())
    return out


def load_stop_list(
    *, customer_path: str | Path | None = None, default_path: str | Path | None = None
) -> set[str]:
    """停用词全集(大写归一)。default(JSON, 7 通用类)+ 可选客户(.txt 扁平)合并。
    缺 default -> 空集降级(等于不过滤); 缺/未给 customer -> 只用 default。镜像
    requirement/signal_terms.py:load_signal_terms。

    Stop-list JSON shape:
      { "categories": { <name>: { "keywords": [...], "reason": "..." }, ... } }
    """
    out: set[str] = set()
    dp = Path(default_path).expanduser() if default_path else DEFAULT_STOP_LIST
    if dp.exists():
        out |= _flatten_default(json.loads(dp.read_text(encoding="utf-8")))
    if customer_path:
        cp = Path(customer_path).expanduser()
        if cp.exists():
            out |= set(_parse_customer_stop(cp.read_text(encoding="utf-8")))
    return out


def extract_camelcase_phrases(text: str, min_words: int = 2) -> set[str]:
    """Find runs of `min_words`+ Title-Case words separated by single spaces /
    hyphens, glue them into PascalCase keywords. e.g. "Change User Package" ->
    "ChangeUserPackage". Used to bridge docx prose to Java class names.

    min_words default = 2: many real Java class names worth matching are
    exactly 2 words (DynamicCharging, ChangeUserPackage, etc.). 3-word
    threshold was tried but dropped the critical bridge keyword
    `DynamicCharging` for the dc-enhancements sample (8 TP lost). 2-word
    generics are filtered via the `camelcase_generics` stop-list category
    instead of via min_words.

    Stop-list filtering is applied LATER by the caller, not here.
    """
    out: set[str] = set()
    seq_re = re.compile(
        r"\b(?:[A-Z][a-z]+)(?:[ \-]+[A-Z][a-z]+){"
        + str(min_words - 1)
        + r",}\b"
    )
    for m in seq_re.finditer(text):
        phrase = m.group(0)
        glued = re.sub(r"[ \-]+", "", phrase)
        out.add(glued)
    return out


def extract_keywords(
    text: str,
    stop_list: set[str] | None = None,
    customer_stop_path: str | Path | None = None,
    min_shouty_len: int = 3,
) -> list[str]:
    """Pipeline: docx text -> SHOUTY tokens + CamelCase phrases, filter stop-list.

    Returns sorted unique list of keywords ready for ripgrep / filename grep.

    Stop-list comparison is case-insensitive (uppercase normalized).
    Caller may pass an explicit stop_list (set) or customer_stop_path (Path) as
    a customer-layer merge on top of the universal default (see load_stop_list).
    """
    if stop_list is None:
        stop_list = load_stop_list(customer_path=customer_stop_path)

    shouty = {
        kw for kw in _SHOUTY_RE.findall(text)
        if len(kw) >= min_shouty_len and kw.upper() not in stop_list
    }
    camel = {
        kw for kw in extract_camelcase_phrases(text)
        if kw.upper() not in stop_list
    }
    return sorted(shouty | camel)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
    else:
        text = (
            "Product Paper for FTTH CPN CRM. This API allows agents to change "
            "user package. Operator FTTH Privileges need updating. SMS alert on "
            "BSS integration."
        )
    stop = load_stop_list()
    kws = extract_keywords(text, stop_list=stop)
    print(f"Stop-list size: {len(stop)}")
    print(f"Extracted keywords ({len(kws)}):")
    for kw in kws:
        print(f"  {kw}")
