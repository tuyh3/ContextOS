"""业务能力分类器测试(02 design §3.2)。

设计思路:
- 用 FakeLLM 注入 canned JSON,验证 classify() 解析 + 返回 MatchedCapability 列表。
- 测试 prompt 内容包含全部 8 类 capability 英文值(防漏字)。
- 测试 business_intent 透传到 prompt。
- 全部用确定化 FakeLLM,无网络依赖。

评分标准:
- test_classify_returns_capabilities: 验证 capability 顺序 + confidence 值。
- test_classify_empty_when_no_match: 验证空列表正确返回,不抛异常。
- test_classify_prompt_lists_eight_capabilities: prompt + system 合并串含 8 个英文 capability key。
- test_classify_passes_business_intent_context: business_intent 出现在 prompt 中。
"""
from __future__ import annotations

import json

from contextos.llm import FakeLLM
from contextos.requirement.classifier import classify


def _canned(caps: list[dict]) -> str:
    return json.dumps({"matched_capabilities": caps}, ensure_ascii=False)


def test_classify_returns_capabilities():
    llm = FakeLLM(responses=[_canned([
        {"capability": "billing-charging", "confidence": 0.9, "evidence": "动态计费"},
        {"capability": "notification", "confidence": 0.7, "evidence": "SMS 提醒"},
    ])])
    caps = classify(llm, "新增动态计费,完成后发 SMS 提醒")
    assert [c.capability for c in caps] == ["billing-charging", "notification"]
    assert caps[0].confidence == 0.9


def test_classify_empty_when_no_match():
    llm = FakeLLM(responses=[_canned([])])
    caps = classify(llm, "纯文档排版调整")
    assert caps == []


def test_classify_prompt_lists_eight_capabilities():
    llm = FakeLLM(responses=[_canned([])])
    classify(llm, "x")
    prompt = llm.calls[0].prompt + (llm.calls[0].system or "")
    for cap in [
        "product-subscription", "billing-charging", "eligibility-check",
        "ussd-menu", "admin-config", "esb-interface", "batch-job", "notification",
    ]:
        assert cap in prompt


def test_classify_passes_business_intent_context():
    llm = FakeLLM(responses=[_canned([])])
    classify(llm, "raw text", business_intent="BIZ_INTENT_MARKER")
    assert any("BIZ_INTENT_MARKER" in c.prompt for c in llm.calls)
