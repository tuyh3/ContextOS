"""mechanism_tag 同义池(spec Appendix H.5)。

数据结构: synonyms[mechanism_tag] = {canonical, variants}。
存储: 种子(通用 tracked)= references/ops_synonyms_seed.json;
      积累(客户特定 gitignored)= <data_dir>/ops-vocab/synonyms.json。
读时按 mechanism_tag 合并(积累 variants 并入种子)。
归并锚 = mechanism_tag + 人确认(防 false synonym, spec Appendix H.3):只有人确认
同一 mechanism_tag 的 search_terms 才互进同义池(在 recorder Phase5 调 accumulate)。

**受控枚举 fail-closed(spec Appendix H.3 MUST)**: expand_terms / accumulate 的 mechanism_tag
必须 in MECHANISM_TAGS, 未知 tag raise UnknownMechanismTagError、**不自动新建**(否则不可信
host 可造任意 tag 污染同义池)。同义池"积累"的只是 variants(同义词), 不是新 tag;新机制族
扩展 = 人工加 MECHANISM_TAGS 种子(受控)。

三动作:
  load_merged       种子 + 积累按 mechanism_tag 合并(variants 去重并集)
  expand_terms      写入/查询展开: 输入词 + 该 mechanism_tag 全 variants 并集(保序去重); 未知 tag fail-closed
  accumulate        Phase5 积累: 新 search_terms 以 mechanism_tag 归类并入积累池(原子写); 未知 tag fail-closed
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from contextos.ops.mechanism_tags import is_known_tag

_SEED_PATH = Path(__file__).parent / "references" / "ops_synonyms_seed.json"


class UnknownMechanismTagError(ValueError):
    """mechanism_tag 不在受控枚举 MECHANISM_TAGS(fail-closed, 不自动新建)。"""


def load_seed() -> dict[str, dict]:
    return json.loads(_SEED_PATH.read_text(encoding="utf-8"))


def _load_accumulation(vocab_path: Path) -> dict[str, dict]:
    if not Path(vocab_path).exists():
        return {}
    try:
        return json.loads(Path(vocab_path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for it in items:
        if it and it not in seen:
            seen[it] = None
    return list(seen.keys())


def load_merged(vocab_path: Path) -> dict[str, dict]:
    """种子 + 积累按 mechanism_tag 合并(积累 variants 并入种子, 去重保序)。"""
    merged: dict[str, dict] = {}
    seed = load_seed()
    acc = _load_accumulation(vocab_path)
    for tag in set(seed) | set(acc):
        s = seed.get(tag, {})
        a = acc.get(tag, {})
        canonical = s.get("canonical") or a.get("canonical") or tag
        variants = _dedup_keep_order(
            list(s.get("variants", [])) + list(a.get("variants", [])))
        merged[tag] = {"canonical": canonical, "variants": variants}
    return merged


def expand_terms(terms: list[str], mechanism_tag: str, vocab_path: Path) -> list[str]:
    """写入/查询展开: 输入 terms + 该 mechanism_tag 的 canonical+variants 并集(保序去重)。

    未知 mechanism_tag fail-closed(spec Appendix H.3): 写入展开侧不接 host 伪 tag。
    """
    if not is_known_tag(mechanism_tag):
        raise UnknownMechanismTagError(
            f"mechanism_tag {mechanism_tag!r} 不在受控枚举 MECHANISM_TAGS(expand 拒)")
    merged = load_merged(vocab_path)
    entry = merged.get(mechanism_tag, {})
    expanded = list(terms) + ([entry["canonical"]] if entry.get("canonical") else []) \
        + list(entry.get("variants", []))
    return _dedup_keep_order(expanded)


def accumulate(terms: list[str], mechanism_tag: str, vocab_path: Path) -> None:
    """Phase5 积累(spec Appendix H.5 + 职责 6): 把 terms 以 mechanism_tag 归类并入积累池。

    归并锚 = mechanism_tag(由人确认根因决定, recorder 传入)+ 去重。原子写(临时文件 + rename),
    防并发半截写。受控枚举里没有的 mechanism_tag -> fail-closed raise、**不自动新建**(spec
    Appendix H.3: 防 host 造 tag 污染同义池; 新机制族扩展走人工加 MECHANISM_TAGS 种子)。
    """
    if not is_known_tag(mechanism_tag):
        raise UnknownMechanismTagError(
            f"mechanism_tag {mechanism_tag!r} 不在受控枚举 MECHANISM_TAGS(accumulate 拒, 不自动新建)")
    vocab_path = Path(vocab_path)
    vocab_path.parent.mkdir(parents=True, exist_ok=True)
    acc = _load_accumulation(vocab_path)
    entry = acc.get(mechanism_tag, {})
    canonical = entry.get("canonical") or (terms[0] if terms else mechanism_tag)
    variants = _dedup_keep_order(list(entry.get("variants", [])) + list(terms))
    acc[mechanism_tag] = {"canonical": canonical, "variants": variants}
    # 原子写: 同目录临时文件 + os.replace(同设备 rename 原子)
    fd, tmp = tempfile.mkstemp(dir=str(vocab_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(acc, f, ensure_ascii=False, indent=2)
        os.replace(tmp, vocab_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
