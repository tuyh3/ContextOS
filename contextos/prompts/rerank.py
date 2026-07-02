"""07 LLM 重排的 prompt(三维适配,对齐 07 design §5)。

对标 prompts/scope.py 约定:SYSTEM 常量 + build 函数 + 字符串拼接(非 .format,
输出指令里的 JSON 含 {} 会撞坏 .format)。改这里要同步改 rerank/schema.RerankBatchOutput
+ rerank/tests + prompts/tests(pytest 守护)。
"""
from __future__ import annotations

RERANK_SYSTEM = (
    "你是需求影响定位的相关性评审。给你一条需求的业务意图, 和一批已被便宜桥捞出的候选, "
    "逐个判断每个候选跟这条需求到底相不相关 —— 这是过滤降噪, 不是搜索。\n"
    "对每个候选投一票:\n"
    "  support = 跟需求业务相关, 很可能受影响;\n"
    "  oppose  = 字面像但语义无关(如全仓通用工具类 / 不相干的表或配置);\n"
    "  abstain = 证据不足判不准, 弃权(别硬猜)。\n"
    "再给 relevance(主观相关性 0-1)+ evidence_strength(看到的证据强度 0-1)+ "
    "一句 reasoning。只输出 JSON。"
)

_DIM_FOCUS = {
    "method": "判方法/类的语义是否对齐需求的业务能力, 调用链关系是否合理。",
    "sql": "判这张表/列/SQL 模板的业务域是否落在需求范围内, 写入侧动作意图是否相关(如下方给了业务文档摘要则结合判业务域, 没给则仅凭结构信号)。",
    "config": "判这个配置项/规则改了对需求有没有影响, bind 到的代码是否相关, bind_strategy 可不可信。"
              "注意: 只给了配置的类型(value_type)和元数据, 没有也不需要配置原始值 —— "
              "不要在 reasoning 里请求或推测原始值, 只凭元数据判断(如下方给了业务文档摘要则一并参考判业务域, 没给则仅凭元数据)。",
}

_OUTPUT_INSTRUCTION = (
    '输出 {"votes": [{"candidate_index": <序号>, "vote": "support"|"oppose"|"abstain", '
    '"relevance": <0-1>, "evidence_strength": <0-1>, "reasoning": "一句话"}, ...]} —— '
    "votes 必须每个候选一项, candidate_index 对应下面候选的序号。"
)


def build_rerank_prompt(
    dim: str,
    *,
    business_intent: str,
    matched_capability: str,
    candidates_block: str,
    rag_summary: str = "",
) -> str:
    """拼某一维 chunk 的 user prompt。dim in {method,sql,config};
    candidates_block 是已编号的候选清单文本;rag_summary 仅 sql/config 维非空。"""
    parts = [
        f"需求业务意图: {business_intent or '(未提供)'}",
        f"匹配的业务能力: {matched_capability or '(未分类)'}",
        f"本维判断重点: {_DIM_FOCUS[dim]}",
    ]
    # method 维不带 RAG 业务摘要(design §5.1 点 3 只 sql/config 维带);此处结构性兜底,
    # 即便调用方误传 rag_summary, method 维也不拼进去 —— fail-safe, 让调用方失误也安全。
    if dim != "method" and rag_summary.strip():
        parts.append("相关业务文档摘要(帮你判业务域):\n" + rag_summary.strip())
    parts.append("候选(逐个判, 序号从 0 起):\n" + candidates_block)
    parts.append(_OUTPUT_INSTRUCTION)
    return "\n\n".join(parts)
