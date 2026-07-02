"""Guard 1 scope(spec 4.1):纯代码预筛 prefilter + LLM scope judge。

预筛: 0 token, 只拦"明显退化"(短 + 真文字占比低 + 无需求信号词), 三条同时命中才判
垃圾, 故意保守(宁放过不误杀)。scope judge: LLM 判"是不是关于某个软件系统的事"
(2026-05-31 02b 修订: 从"是不是变更需求"放宽, 见 prompts/scope.py), in_scope/
out_of_scope/unsure 三档, 自一致采样, unsure 或失败 fail-open。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from contextos.llm import LLMError, LLMProvider
from contextos.profile.schema import ScopeConfig
from contextos.prompts.scope import SCOPE_SYSTEM, build_scope_prompt
from contextos.requirement.schema import _StrictBase


def prefilter(text: str, cfg: ScopeConfig, terms: set[str]) -> bool:
    """True = 明显非需求垃圾(早退 REJECT)。三规则同时命中才 True(保守)。

    1. 去空白后长度 < min_chars
    2. 真文字(字母 + 汉字, str.isalpha 覆盖两者)占非空白字符比 < min_alpha_ratio
    3. 不含任何需求信号词(子串匹配, casefold)
    """
    stripped = "".join(text.split())
    nonspace = len(stripped)
    too_short = nonspace < cfg.min_chars

    alpha = sum(1 for ch in text if ch.isalpha())   # isalpha 对 CJK 也 True
    ratio = (alpha / nonspace) if nonspace else 0.0
    low_alpha = ratio < cfg.min_alpha_ratio

    low = text.casefold()
    no_signal = not any(t in low for t in terms)

    return too_short and low_alpha and no_signal


# ---------------------------------------------------------------------------
# Guard 1b: LLM scope judge
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScopeVerdict:
    """scope judge 裁决。scope_score = 自一致率(yes/有效样本);failed=全部样本失败。"""
    scope_score: float
    samples: int
    failed: bool = False
    reason: str = ""


class _ScopeAnswer(_StrictBase):
    # 三档(2026-05-31 02b): in_scope=算 / out_of_scope=拒 / unsure=拿不准弃权(fail-open)
    verdict: Literal["in_scope", "out_of_scope", "unsure"]
    reason: str = ""


def scope_judge(llm: LLMProvider, raw_text: str, cfg: ScopeConfig) -> ScopeVerdict:
    """LLM 判"是不是关于某个软件系统的事"(vs 跑题垃圾)。samples=1 单次;samples=N 自一致率。

    判定走三档 verdict: in_scope(算) / out_of_scope(拒) / unsure(弃权)。
    A 层(领域无关)始终跑;B 层(domain_description 非空)在 prompt 追加领域约束,
    v1 默认空 -> 跳过。unsure 或调用失败 = 弃权;全部弃权 -> fail-open(failed=True),
    由 pipeline 走 DEGRADED(绝不据此 REJECT,也不假装 OK)。
    """
    prompt = build_scope_prompt(raw_text, cfg.domain_description)

    n = max(1, cfg.samples)
    answers: list[bool] = []   # True=in_scope, False=out_of_scope;unsure/失败不入列(弃权)
    n_unsure = 0
    for _ in range(n):
        try:
            ans = llm.structured(prompt, _ScopeAnswer, system=SCOPE_SYSTEM)
        except LLMError:
            continue   # 调用失败 = 弃权
        if ans.verdict == "in_scope":
            answers.append(True)
        elif ans.verdict == "out_of_scope":
            answers.append(False)
        else:
            n_unsure += 1   # unsure = 弃权

    if not answers:
        return ScopeVerdict(
            scope_score=0.0, samples=n, failed=True,
            reason="scope judge 无确定判定(全部 unsure 或调用失败), fail-open 待人工确认",
        )
    yes = sum(1 for a in answers if a)
    score = yes / len(answers)
    extra = f", {n_unsure} 弃权" if n_unsure else ""
    return ScopeVerdict(
        scope_score=score, samples=n, failed=False,
        reason=f"scope judge {yes}/{len(answers)} 判为软件相关{extra}",
    )
