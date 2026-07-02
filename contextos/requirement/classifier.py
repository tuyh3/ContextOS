"""业务能力分类器(design 02 §3.2)。LLM few-shot 把自然语言映射到 8 类业务能力。

v1 不接 RAG context 校准(依赖 03/字典,defer);留 extra_context 接缝。
"""
from __future__ import annotations

from contextos.llm import LLMProvider
from contextos.requirement.schema import MatchedCapability, _StrictBase

_CAPABILITIES = """\
1. product-subscription   产品订购
2. billing-charging       计费/账务
3. eligibility-check      资格校验
4. ussd-menu              USSD 菜单
5. admin-config           管理后台配置
6. esb-interface          ESB / 能开接口
7. batch-job              批处理 / ET 进程
8. notification           通知 / SMS / Email"""

_SYSTEM = (
    "你是电信 BSS 需求分类助手。需求文本通常不写类名/方法名,而写业务动作。"
    "把需求映射到下列 8 类业务能力(capability 字段只能取这 8 个英文值之一),"
    "给每个命中的能力一个 0-1 置信度 + 一句证据。无命中返回空列表。只输出 JSON。"
)

_FEWSHOT = (
    "示例:\n"
    "需求:'新增一个套餐订购校验,扣费成功后发短信' ->\n"
    '{"matched_capabilities": ['
    '{"capability": "product-subscription", "confidence": 0.8, "evidence": "套餐订购"}, '
    '{"capability": "eligibility-check", "confidence": 0.7, "evidence": "订购校验"}, '
    '{"capability": "billing-charging", "confidence": 0.85, "evidence": "扣费"}, '
    '{"capability": "notification", "confidence": 0.75, "evidence": "发短信"}]}\n'
)

_PROMPT_TMPL = (
    "8 类业务能力:\n{caps}\n\n"
    "{fewshot}\n"
    "{intent_line}"
    "需求文本:\n{raw_text}\n\n"
    "{extra_context}"
    "输出 matched_capabilities 列表。"
)


class ClassificationResult(_StrictBase):
    matched_capabilities: list[MatchedCapability]


def classify(
    llm: LLMProvider,
    raw_text: str,
    *,
    business_intent: str = "",
    extra_context: str = "",
) -> list[MatchedCapability]:
    """LLMStructuredError 向上抛(pipeline 兜底)。"""
    intent_line = f"业务意图提示: {business_intent}\n\n" if business_intent else ""
    ctx = f"参考上下文:\n{extra_context}\n\n" if extra_context else ""
    prompt = _PROMPT_TMPL.format(
        caps=_CAPABILITIES,
        fewshot=_FEWSHOT,
        intent_line=intent_line,
        raw_text=raw_text,
        extra_context=ctx,
    )
    result = llm.structured(prompt, ClassificationResult, system=_SYSTEM)
    return result.matched_capabilities
