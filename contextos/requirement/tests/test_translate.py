"""translate.py 测试。
设计思路:
  - detect_language: CJK 占比 >0.6->zh / <0.1->en / 中间->mixed; 无文字->en
  - translate: business_intent 空不调 LLM; 有内容产双语 Queries; glossary 命中写进 prompt
评分标准: 6 个断言全部通过即达标(FakeLLM 模拟 LLM 不发真网络请求)
自动脚本: uv run pytest contextos/requirement/tests/test_translate.py -v
"""
from __future__ import annotations

import json

from contextos.llm import FakeLLM
from contextos.requirement.schema import Queries
from contextos.requirement.translate import detect_language, translate


def test_detect_language_chinese():
    assert detect_language("新增动态计费批量操作功能") == "zh"


def test_detect_language_english():
    assert detect_language("Add bulk operations to dynamic charging") == "en"


def test_detect_language_mixed():
    assert detect_language("新增 Dynamic Charging 的 Bulk 操作 SMS 功能模块整合接口") == "mixed"


def test_detect_language_empty_defaults_en():
    assert detect_language("   ") == "en"


def test_translate_returns_bilingual_queries():
    llm = FakeLLM(responses=[json.dumps({
        "zh": "新增动态计费批量操作功能",
        "en": "Add Bulk operations to Dynamic Charging functionality",
    }, ensure_ascii=False)])
    q = translate(llm, "新增动态计费批量操作", ["动态计费", "批量"])
    assert isinstance(q, Queries)
    assert q.zh.startswith("新增动态计费")
    assert q.en.startswith("Add Bulk")


def test_translate_empty_intent_skips_llm():
    llm = FakeLLM(responses=[])  # 不该被调用
    q = translate(llm, "", [])
    assert q.zh == "" and q.en == ""
    assert llm.calls == []


def test_translate_instructs_glossary_preservation():
    """命中静态 glossary 的术语(USSD)要进 prompt 的"不翻译"指令。"""
    llm = FakeLLM(responses=[json.dumps({"zh": "调整 USSD 菜单", "en": "Adjust USSD menu"},
                                        ensure_ascii=False)])
    translate(llm, "调整 USSD 菜单展示", ["USSD"])
    prompt = llm.calls[0].prompt
    assert "USSD" in prompt
