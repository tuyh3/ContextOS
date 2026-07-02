from __future__ import annotations

import json

from contextos.llm import FakeLLM
from contextos.requirement.extract import ExtractionResult, extract


def _canned_extraction() -> str:
    return json.dumps({
        "business_intent": "新增动态计费批量操作",
        "key_entities": ["动态计费", "批量操作"],
        "actions": ["add", "modify"],
        "candidate_code_names": [
            {"term": "BulkStart", "kind": "camelcase", "source": "regex",
             "source_span": "批量操作"},
        ],
        "candidate_table_terms": [
            {"term": "OFFER", "kind": "entity", "source": "llm", "source_span": "动态计费"},
        ],
        "candidate_config_keys": [
            {"term": "批量上限", "kind": "param_term", "source": "llm",
             "source_span": "批量操作"},
        ],
    }, ensure_ascii=False)


def test_extract_parses_llm_structured_output():
    llm = FakeLLM(responses=[_canned_extraction()])
    res = extract(llm, "需求:新增 DynamicCharging 批量操作,SMS 提醒")
    assert isinstance(res, ExtractionResult)
    assert res.business_intent == "新增动态计费批量操作"
    assert res.actions == ["add", "modify"]
    assert any(t.term == "OFFER" for t in res.candidate_table_terms)


def test_extract_forces_llm_source_on_code_names():
    """LLM 自填 source=regex 也被强制改成 llm(只有正则基线才配 regex)。"""
    llm = FakeLLM(responses=[_canned_extraction()])
    res = extract(llm, "X")
    bulk = [c for c in res.candidate_code_names if c.term == "BulkStart"]
    assert bulk and bulk[0].source == "llm"


def test_extract_forces_llm_source_on_table_terms_and_config_keys():
    """表词/配置键自填 source=regex 同样强制改 llm。

    grounding.is_grounded 对 source=regex 豁免 source_span 子串核验
    (正则基线 by-construction 有出处),若放任 LLM 自称 regex,
    脑补的表词/配置键就能绕过 Guard 2 直通下游。
    """
    canned = json.loads(_canned_extraction())
    canned["candidate_table_terms"] = [
        {"term": "OFFER", "kind": "entity", "source": "regex", "source_span": ""},
    ]
    canned["candidate_config_keys"] = [
        {"term": "批量上限", "kind": "param_term", "source": "regex",
         "source_span": ""},
    ]
    llm = FakeLLM(responses=[json.dumps(canned, ensure_ascii=False)])
    res = extract(llm, "X")
    assert res.candidate_table_terms and all(
        c.source == "llm" for c in res.candidate_table_terms
    )
    assert res.candidate_config_keys and all(
        c.source == "llm" for c in res.candidate_config_keys
    )


def test_extract_merges_regex_baseline():
    """raw_text 里的 SHOUTY/CamelCase 经正则基线进 candidate_code_names(source=regex)。"""
    # LLM 不产任何 code name,全靠正则基线
    canned = json.loads(_canned_extraction())
    canned["candidate_code_names"] = []
    llm = FakeLLM(responses=[json.dumps(canned, ensure_ascii=False)])
    # 注意:正则基线的 CamelCase 检测只认空格分隔的 Title-Case 词组
    # ("Dynamic Charging" -> "DynamicCharging"),不认已黏合的单 token。
    res = extract(llm, "Product Paper FTTH support Dynamic Charging feature")
    terms = {c.term for c in res.candidate_code_names}
    assert "FTTH" in terms  # SHOUTY
    assert "DynamicCharging" in terms  # CamelCase(由 "Dynamic Charging" 黏合)
    for c in res.candidate_code_names:
        assert c.source == "regex"


def test_extract_dedups_term_prefers_llm():
    """LLM 和正则都产 DynamicCharging,合并后只一个,且保 LLM 那条。"""
    canned = json.loads(_canned_extraction())
    canned["candidate_code_names"] = [
        {"term": "DynamicCharging", "kind": "camelcase", "source": "llm"},
    ]
    llm = FakeLLM(responses=[json.dumps(canned, ensure_ascii=False)])
    # 空格分隔,正则基线也黏合出 DynamicCharging -> 与 LLM 同 term,验证去重
    res = extract(llm, "Dynamic Charging feature")
    dc = [c for c in res.candidate_code_names if c.term.lower() == "dynamiccharging"]
    assert len(dc) == 1
    assert dc[0].source == "llm"


def test_extract_prompt_includes_raw_text():
    llm = FakeLLM(responses=[_canned_extraction()])
    extract(llm, "UNIQUE_MARKER_TEXT_123")
    assert any("UNIQUE_MARKER_TEXT_123" in call.prompt for call in llm.calls)


def test_regex_baseline_sets_source_span_to_term():
    """正则基线候选 source_span 回填为词本身(本就从原文抓的, Guard 2 grounding 留痕)。"""
    from contextos.requirement.extract import _regex_baseline
    names = _regex_baseline("新增 Dynamic Charging 批量操作")
    assert names, "正则基线应抽出 DynamicCharging"
    for c in names:
        assert c.source == "regex"
        assert c.source_span == c.term   # 回填出处=词本身


def test_extract_preserves_llm_source_span():
    """LLM 给的 source_span 经 extract(含 c.source='llm' 强制覆盖)后完整保留, 不被抹。"""
    payload = json.dumps({
        "business_intent": "新增动态计费",
        "key_entities": ["动态计费"],
        "actions": ["add"],
        "candidate_code_names": [
            {"term": "DynamicCharging", "kind": "camelcase", "source": "llm",
             "source_span": "动态计费"}],
        "candidate_table_terms": [],
        "candidate_config_keys": [],
    }, ensure_ascii=False)
    res = extract(FakeLLM(responses=[payload]), "新增动态计费")
    llm_named = [c for c in res.candidate_code_names if c.source == "llm"]
    assert llm_named and llm_named[0].source_span == "动态计费"


def test_extract_accepts_context_path_separate_from_source():
    """extract 收 context_path(仅理解)+ source_text(唯一 source_span 来源)。
    断言: prompt 里 context_path 与 source_text 分立标注; 行为不回归。
    """
    llm = FakeLLM(responses=[_canned_extraction()])
    res = extract(llm, "新增 DynamicCharging 批量操作",
                  context_path="账务 > 限制逻辑")
    assert res.business_intent  # 不回归
    sent = llm.calls[0].prompt
    assert "账务 > 限制逻辑" in sent
    # context 与 source 分立标注(实现用"上下文路径"/"待抽取正文"两段)
    assert "上下文路径" in sent and "待抽取正文" in sent
    assert sent.index("上下文路径") < sent.index("待抽取正文")


def test_regex_baseline_filters_customer_stop(tmp_path):
    """_regex_baseline 传 customer stop 文件时, 客户宽泛词从 code_names 被滤。"""
    from contextos.requirement.extract import _regex_baseline
    cust = tmp_path / "cust.txt"
    cust.write_text("FOOSVC\n", encoding="utf-8")
    # 注意: 正则基线的 CamelCase 检测只认空格分隔的 Title-Case 词组
    # ("Dynamic Charging" -> "DynamicCharging"), 不认已黏合的单 token
    # (见 test_extract_merges_regex_baseline 同一约束)。
    text = "Add FOOSVC and Dynamic Charging to the flow"
    without = {c.term for c in _regex_baseline(text)}
    withcust = {c.term for c in _regex_baseline(text, stop_keywords_path=str(cust))}
    assert "FOOSVC" in without           # 不传时通用 default 不含 FOOSVC -> 保留
    assert "FOOSVC" not in withcust      # 传客户文件 -> 被滤
    assert "DynamicCharging" in withcust  # 真业务词不受影响
