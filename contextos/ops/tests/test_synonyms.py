"""同义池测试(spec Appendix H.5: 数据结构 / 存储 / 读写流)。

设计思路: synonyms[mechanism_tag]={canonical, variants}。种子 tracked + 积累 gitignored;
读时按 mechanism_tag 合并(积累 variants 并入种子);expand_terms 写入展开;accumulate Phase5 积累。
评分标准: 加载种子;合并积累;展开返回 variants 并集;积累并入新词去重;无积累文件不崩;
  未知 mechanism_tag 在 accumulate/expand 均 fail-closed raise(不自动新建, spec Appendix H.3)。
自动脚本逻辑: tmp_path 当 vocab 路径, 写读断言。归并锚 = mechanism_tag 受控枚举(防 false synonym)。
"""
from __future__ import annotations

import json
from pathlib import Path

from contextos.ops import synonyms


def test_load_seed_has_mechanism_tags():
    seed = synonyms.load_seed()
    assert "deferred_charge" in seed
    assert seed["deferred_charge"]["canonical"] == "递延收费"


def test_merged_without_accumulation_equals_seed(tmp_path: Path):
    merged = synonyms.load_merged(tmp_path / "nope.json")
    assert merged["deferred_charge"]["variants"] == \
        synonyms.load_seed()["deferred_charge"]["variants"]


def test_expand_terms_returns_union(tmp_path: Path):
    # 输入含 mechanism_tag 的某变体 -> 展开成该 tag 全 variants 并集 + 输入词
    out = synonyms.expand_terms(["延迟扣费"], "deferred_charge", tmp_path / "nope.json")
    assert "递延收费" in out and "时点解耦" in out and "延迟扣费" in out


def test_accumulate_merges_new_terms(tmp_path: Path):
    vocab = tmp_path / "synonyms.json"
    synonyms.accumulate(["新变体词", "延迟扣费"], "deferred_charge", vocab)
    data = json.loads(vocab.read_text(encoding="utf-8"))
    assert "新变体词" in data["deferred_charge"]["variants"]
    # 去重: 已在种子里的不重复堆
    assert data["deferred_charge"]["variants"].count("延迟扣费") <= 1


def test_accumulate_then_merge_visible(tmp_path: Path):
    vocab = tmp_path / "synonyms.json"
    synonyms.accumulate(["全新词X"], "deferred_charge", vocab)
    merged = synonyms.load_merged(vocab)
    assert "全新词X" in merged["deferred_charge"]["variants"]


def test_accumulate_unknown_mechanism_tag_fail_closed(tmp_path: Path):
    """spec Appendix H.3 MUST: 积累一个受控枚举里没有的 mechanism_tag -> fail-closed raise,
    **不自动新建**(防 host 造 tag 污染同义池)。新机制族扩展走人工加 MECHANISM_TAGS 种子。"""
    import pytest

    vocab = tmp_path / "synonyms.json"
    with pytest.raises(synonyms.UnknownMechanismTagError):
        synonyms.accumulate(["资格不符"], "host_made_up_tag", vocab)
    # fail-closed: 未写出污染条目
    assert not vocab.exists() or "host_made_up_tag" not in json.loads(
        vocab.read_text(encoding="utf-8"))


def test_expand_terms_unknown_tag_fail_closed(tmp_path: Path):
    """expand_terms 未知 mechanism_tag 同样 fail-closed(写入展开侧不接 host 伪 tag)。"""
    import pytest

    with pytest.raises(synonyms.UnknownMechanismTagError):
        synonyms.expand_terms(["x"], "host_made_up_tag", tmp_path / "nope.json")
