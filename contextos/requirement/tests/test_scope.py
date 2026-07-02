"""Guard 1 scope 测试:预筛 prefilter(Task 5)+ scope judge(Task 6)。

测试思路(prefilter):
  - 三规则"同时命中"才判垃圾(故意保守): 短 + 真文字占比低 + 无信号词
  - 9.9-9.11=? -> 三条全中 -> True(判垃圾)
  - 真需求 -> 含信号词 / 高文字占比 -> False(放行)
  - 长数学题(len >= min_chars)-> 规则1 不中 -> False(放行, 交 scope judge 兜底)
评分标准: 保守 = 只在"短+全符号+无信号词"同时成立才拦, 其余一律放行。
自动脚本测试逻辑: 纯代码, 确定。

测试思路(scope judge):
  - samples=1: LLM 答 yes/no, scope_score = 1.0/0.0 (binary)
  - samples=N: 问 N 遍, score = yes 次数 / 有效样本数 (self-consistency)
  - fail-open 全失败: all bad JSON -> failed=True, score=0.0 (pipeline -> DEGRADED, not REJECT)
  - fail-open 部分失败: 一遍坏(耗 3 次重试)+ 两遍好 -> 只用成功样本算一致率
评分标准: fail-open 两条路径(全失败 / 部分失败)行为必须正确。
自动脚本测试逻辑: FakeLLM 队列驱动, 确定。
"""
from __future__ import annotations

import json

from contextos.llm import FakeLLM
from contextos.profile.schema import ScopeConfig
from contextos.requirement.scope import ScopeVerdict, prefilter, scope_judge
from contextos.requirement.signal_terms import load_signal_terms

_TERMS = load_signal_terms()


def test_prefilter_rejects_math_garbage():
    cfg = ScopeConfig()
    assert prefilter("9.9-9.11=?", cfg, _TERMS) is True


def test_prefilter_passes_real_requirement():
    cfg = ScopeConfig()
    # 含信号词"新增" -> 规则3 不中 -> 放行
    assert prefilter("新增动态计费批量操作,完成后发短信", cfg, _TERMS) is False


def test_prefilter_passes_long_symbol_string():
    cfg = ScopeConfig()
    # 16 字符 >= min_chars(12) -> 规则1 不中 -> 放行(交 scope judge)
    assert prefilter("1+2+3+4+5+6+7=?", cfg, _TERMS) is False


def test_prefilter_passes_alpha_heavy_short_no_signal():
    cfg = ScopeConfig()
    # 短 + 无信号词, 但真文字占比高(全字母)-> 规则2 不中 -> 放行
    assert prefilter("hello world", cfg, _TERMS) is False


def test_prefilter_signal_word_present_passes_even_if_short_symbolic():
    cfg = ScopeConfig()
    # 含"add" -> 规则3 不中 -> 放行
    assert prefilter("add: 9.9", cfg, _TERMS) is False


# ---------------------------------------------------------------------------
# Task 6: scope judge (LLM self-consistency + fail-open)
# ---------------------------------------------------------------------------

def _ans(verdict: str) -> str:
    # verdict ∈ {in_scope, out_of_scope, unsure}(2026-05-31 02b 三档)
    return json.dumps({"verdict": verdict, "reason": "x"}, ensure_ascii=False)


def test_scope_judge_in_scope_score_1():
    cfg = ScopeConfig()                  # samples=1
    v = scope_judge(FakeLLM(responses=[_ans("in_scope")]), "新增计费功能", cfg)
    assert isinstance(v, ScopeVerdict)
    assert v.scope_score == 1.0
    assert v.failed is False
    assert v.samples == 1


def test_scope_judge_out_of_scope_score_0():
    cfg = ScopeConfig()
    v = scope_judge(FakeLLM(responses=[_ans("out_of_scope")]), "9.9-9.11=?", cfg)
    assert v.scope_score == 0.0
    assert v.failed is False


def test_scope_judge_self_consistency_samples_3():
    cfg = ScopeConfig(samples=3)
    llm = FakeLLM(responses=[_ans("in_scope"), _ans("in_scope"), _ans("out_of_scope")])
    v = scope_judge(llm, "某模糊输入", cfg)
    assert v.scope_score == 2 / 3
    assert v.samples == 3
    assert len(llm.calls) == 3           # 真问了 3 遍


def test_scope_judge_unsure_is_fail_open():
    """samples=1 答 unsure(拿不准)-> 弃权 -> 无确定判定 -> fail-open(不 REJECT)。"""
    cfg = ScopeConfig()
    v = scope_judge(FakeLLM(responses=[_ans("unsure")]), "某模棱两可输入", cfg)
    assert v.failed is True              # 让 pipeline 走 DEGRADED, 绝不据 unsure 硬拒
    assert v.scope_score == 0.0
    assert v.samples == 1


def test_scope_judge_unsure_abstains_in_consistency():
    """samples=3: in/unsure/out -> unsure 弃权, 只在 in+out 上算率 = 1/2。"""
    cfg = ScopeConfig(samples=3)
    llm = FakeLLM(responses=[_ans("in_scope"), _ans("unsure"), _ans("out_of_scope")])
    v = scope_judge(llm, "x", cfg)
    assert v.failed is False
    assert v.scope_score == 0.5          # 2 个有效样本(in/out)里 1 个 in
    assert len(llm.calls) == 3


def test_scope_judge_all_unsure_fail_open():
    """全 unsure -> 无有效判定 -> fail-open。"""
    cfg = ScopeConfig(samples=2)
    v = scope_judge(FakeLLM(responses=[_ans("unsure"), _ans("unsure")]), "x", cfg)
    assert v.failed is True
    assert v.scope_score == 0.0


def test_scope_judge_all_fail_is_fail_open():
    """structured 反复产坏 JSON -> 全失败 -> fail-open(failed=True, 不据此 REJECT)。"""
    cfg = ScopeConfig()                  # samples=1, structured 默认重试 2 = 3 次尝试
    v = scope_judge(FakeLLM(responses=["bad", "bad", "bad"]), "x", cfg)
    assert v.failed is True
    assert v.scope_score == 0.0          # 记 0, 但 failed=True 让 pipeline 走 DEGRADED(不 REJECT)
    assert v.samples == 1                # 失败路径仍记录请求样本数(防 refactor 误置 0)


def test_scope_judge_partial_fail_uses_valid_samples():
    """samples=3, 一遍坏(耗 3 次重试)两遍好 -> 用好的算一致率。"""
    cfg = ScopeConfig(samples=3)
    llm = FakeLLM(responses=["bad", "bad", "bad", _ans("in_scope"), _ans("in_scope")])
    v = scope_judge(llm, "x", cfg)
    assert v.failed is False
    assert v.scope_score == 1.0          # 2 个有效样本都 in_scope
