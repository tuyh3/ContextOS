"""contextos/prompts/scope.py 结构性测试(兑现"prompt 外置 = 回归测试守护"的承诺)。

测试思路:
  - build_scope_prompt 必含输出指令 + few-shot + 待判文本; domain_description 条件性追加
  - 输出指令里列的 verdict 取值, 必须跟 requirement/scope.py 的 _ScopeAnswer.verdict
    Literal 一致 —— 防"改了解析 schema 忘了改 prompt(或反之)"的脱节(评审点名的耦合风险)
评分标准: prompt 拼装正确 + few-shot 全渲染 + verdict 取值与解析 schema 不脱节
自动脚本测试逻辑: 纯字符串 + 反射读 Literal, 无 LLM, 完全确定
"""
from __future__ import annotations

import typing

from contextos.prompts.scope import (
    _FEWSHOT_EXAMPLES,
    _OUTPUT_INSTRUCTION,
    SCOPE_FEWSHOT,
    build_scope_prompt,
)
from contextos.requirement.scope import _ScopeAnswer


def test_build_prompt_contains_instruction_fewshot_and_text():
    p = build_scope_prompt("某需求文本")
    assert _OUTPUT_INSTRUCTION in p
    assert "示例:" in p           # few-shot 块在
    assert "某需求文本" in p        # 待判文本被拼进去


def test_domain_line_conditional():
    assert "本项目领域" not in build_scope_prompt("x")          # 空 -> 不加
    assert "本项目领域" not in build_scope_prompt("x", "   ")    # 纯空白 -> 不加
    assert "本项目领域: 电信 BSS" in build_scope_prompt("x", "电信 BSS")


def test_all_fewshot_examples_rendered():
    for text, verdict, _reason in _FEWSHOT_EXAMPLES:
        assert text in SCOPE_FEWSHOT
        assert verdict in SCOPE_FEWSHOT


def test_output_instruction_verdicts_match_parsing_schema():
    """输出指令里的 verdict 取值必须 == _ScopeAnswer.verdict 的 Literal(防 prompt/解析脱节)。"""
    allowed = set(typing.get_args(_ScopeAnswer.model_fields["verdict"].annotation))
    assert allowed == {"in_scope", "out_of_scope", "unsure"}
    for v in allowed:
        assert v in _OUTPUT_INSTRUCTION
