"""Guard 2 grounding 测试(spec 4.2):source_span 对 raw_text 确定性核验。

测试思路:
  - LLM 候选 source_span 在原文(归一化子串)-> 留;空 / 不在 -> 砍(脑补)
  - 正则基线候选(source=regex)-> 恒有出处(by-construction), 豁免子串检查
  - coverage = 留存数 / 总数; 总数 0 -> 1.0(无候选, 无脑补)
评分标准: 砍准脑补、不误砍正则基线; coverage 数值对。
自动脚本测试逻辑: 纯代码, 无 LLM, 确定。
"""
from __future__ import annotations

from contextos.requirement.grounding import _normalize, coverage, ground_candidates
from contextos.requirement.schema import CandidateName


def _cn(term, source, span):
    return CandidateName(term=term, kind="camelcase", source=source, source_span=span)


def test_llm_candidate_span_in_raw_kept():
    raw = "新增动态计费批量操作"
    kept, dropped = ground_candidates([_cn("DynamicCharging", "llm", "动态计费")], raw)
    assert [c.term for c in kept] == ["DynamicCharging"]
    assert dropped == []


def test_llm_candidate_span_missing_dropped():
    raw = "新增动态计费批量操作"
    kept, dropped = ground_candidates([_cn("DiscountPeriod", "llm", "促销档期")], raw)
    assert kept == []
    assert [c.term for c in dropped] == ["DiscountPeriod"]


def test_llm_candidate_empty_span_dropped():
    raw = "新增动态计费"
    kept, dropped = ground_candidates([_cn("FooBar", "llm", "")], raw)
    assert kept == []
    assert len(dropped) == 1


def test_regex_candidate_exempt_even_if_glued_not_in_raw():
    # 正则基线把 "Change User Package" 黏成 ChangeUserPackage, 不是原文子串, 但豁免
    raw = "Operator can Change User Package online"
    kept, dropped = ground_candidates([_cn("ChangeUserPackage", "regex", "")], raw)
    assert [c.term for c in kept] == ["ChangeUserPackage"]
    assert dropped == []
    assert kept[0].source_span == "ChangeUserPackage"   # 回填 term


def test_grounding_normalization_case_and_space():
    raw = "Add  Bulk   Charging"
    kept, _ = ground_candidates([_cn("BulkCharging", "llm", "bulk charging")], raw)
    assert len(kept) == 1   # 大小写 + 多空格归一后命中


def test_grounding_works_on_table_and_config_candidate_types():
    """duck-typing 契约: 三类候选同形, grounding 一套逻辑通吃(spec 4.2 核验对象=三类)。"""
    from contextos.requirement.schema import CandidateConfigKey, CandidateTableTerm

    raw = "新增动态计费, 批量上限可配"
    tt = CandidateTableTerm(term="BILLING", kind="entity", source="llm", source_span="计费")
    tt_bad = CandidateTableTerm(term="PROMO", kind="entity", source="llm", source_span="促销")
    ck = CandidateConfigKey(term="批量上限", kind="param_term", source="llm", source_span="批量上限")
    kept, dropped = ground_candidates([tt, tt_bad, ck], raw)
    assert {c.term for c in kept} == {"BILLING", "批量上限"}
    assert [c.term for c in dropped] == ["PROMO"]


def test_coverage_math():
    assert coverage(2, 4) == 0.5
    assert coverage(0, 0) == 1.0    # 无候选 = 无脑补 = 满分
    assert coverage(3, 3) == 1.0


def test_normalize_folds_fullwidth_to_halfwidth():
    """全/半角逗号、全角字母经 NFKC 折成同一形, 防 eml 混宽标点误丢 span。
    评分: 全角串归一后等于半角串归一; casefold 不折宽度故必须 NFKC。
    """
    assert _normalize("Ａ，ｂ") == _normalize("A,b")
    assert _normalize("订购实例ID") == _normalize("订购实例ＩＤ")
