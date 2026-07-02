"""Guard 1b scope judge 的 prompt(2026-05-31 02b 修订)。

修订要点(见讨论 docs/讨论/2026-05-31-02b-scope边界误判与prompt管理复盘.md):
- 判据从"是不是变更需求"放宽到"是不是关于某个软件系统的事" —— 原判据过窄, 把
  "查代码 / 查表 / 查配置 / 问概念"这类正当输入误判成非需求(且在边界上 LLM 反复翻)。
- 加分类别 few-shot(覆盖有限的"类别"而非无限的"输入"; 领域多样防偏色)+ unsure 档
  (拿不准就弃权, 由 scope_judge 走 fail-open -> DEGRADED, 不硬拒)。
- 门卫只挑"明显跑题垃圾"; 细粒度"能不能答好"交给下游 grounding + confidence(fail-safe 分层)。

输出 schema 与 contextos/requirement/scope.py 的 _ScopeAnswer 绑定; 改这里要同步改它 + 测试。
"""
from __future__ import annotations

import json

SCOPE_SYSTEM = (
    "你是输入分诊员。判断给定文本是不是『关于某个软件系统的事』—— "
    "它的业务、功能、接口、配置、数据/表、流程或代码都算。\n"
    "判 in_scope(下列任一即算, 不要求是『改动』):\n"
    "  1. 变更需求: 要新增/修改/修复某功能、接口、配置、数据、流程;\n"
    "  2. 查询/定位: 问『X 对应哪个方法 / 哪张表 / 哪个配置 / 在哪实现』;\n"
    "  3. 理解概念: 问某个业务或系统概念是什么、涉及什么。\n"
    "判 out_of_scope: 数学题、闲聊、天气、纯数据、无意义符号、写诗作文、"
    "与任何软件系统都无关的内容。\n"
    "若既不像明显软件相关、也不像明显跑题垃圾, 拿不准就答 unsure, 不要硬猜。\n"
    "只输出 JSON。"
)

# 分类别 few-shot: 覆盖有限的"类别"(而非无限的"输入"); 领域多样(电信/电商/通用)防偏色。
# 这里是 seed; 例子集会从手测 / 使用中的真实误判增量生长(eval 驱动)。
_FEWSHOT_EXAMPLES = [
    ("新增先付费套餐的批量订购功能", "in_scope", "变更需求"),
    ("先付费对应的方法是什么", "in_scope", "查询定位代码"),
    ("订单状态存在哪张表", "in_scope", "查询数据表"),
    ("短信网关的超时配置在哪里改", "in_scope", "查询配置"),
    ("什么是渠道授权", "in_scope", "理解业务概念"),
    ("电商订单超时未支付怎么自动取消", "in_scope", "通用系统实现问题"),
    ("9.9-9.11=?", "out_of_scope", "数学题"),
    ("今天天气怎么样", "out_of_scope", "闲聊"),
    ("帮我写一首关于春天的诗", "out_of_scope", "与软件无关"),
    ("asdf qwer 1234 ////", "out_of_scope", "无意义符号"),
]


def _render_fewshot() -> str:
    lines = ["示例:"]
    for text, verdict, reason in _FEWSHOT_EXAMPLES:
        ans = json.dumps({"verdict": verdict, "reason": reason}, ensure_ascii=False)
        lines.append(f'"{text}" -> {ans}')
    return "\n".join(lines)


SCOPE_FEWSHOT = _render_fewshot()

_OUTPUT_INSTRUCTION = (
    '输出 {"verdict": "in_scope" | "out_of_scope" | "unsure", "reason": "一句话理由"}。'
)


def build_scope_prompt(raw_text: str, domain_description: str = "") -> str:
    """拼 scope judge 的 user prompt。domain_description 非空 -> 追加 B 层领域约束。

    用字符串拼接而非 str.format: few-shot 里的 JSON 含 {} , 会撞坏 .format 的占位符解析。
    """
    domain_line = (
        f"本项目领域: {domain_description}\n判断时还需属于该领域。\n\n"
        if domain_description.strip()
        else ""
    )
    return (
        domain_line
        + SCOPE_FEWSHOT
        + "\n\n现在判断这段文本:\n"
        + raw_text
        + "\n\n"
        + _OUTPUT_INSTRUCTION
    )
