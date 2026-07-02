from __future__ import annotations

from contextos.prompts.rerank import RERANK_SYSTEM, build_rerank_prompt


def test_system_mentions_three_votes():
    for v in ("support", "oppose", "abstain"):
        assert v in RERANK_SYSTEM


def test_prompt_carries_business_anchor():
    p = build_rerank_prompt("method", business_intent="新增动态计费批量操作",
                            matched_capability="billing-charging",
                            candidates_block="[0] target=Foo kind=METHOD signals={}")
    assert "新增动态计费批量操作" in p
    assert "billing-charging" in p
    assert "[0] target=Foo" in p
    assert "candidate_index" in p          # 输出指令在


def test_method_prompt_has_no_rag_section():
    p = build_rerank_prompt("method", business_intent="x", matched_capability="y",
                            candidates_block="[0] ...")
    assert "业务文档摘要" not in p


def test_sql_prompt_includes_rag_summary_when_given():
    p = build_rerank_prompt("sql", business_intent="x", matched_capability="y",
                            candidates_block="[0] ...", rag_summary="PM_OFFER 是套餐表")
    assert "PM_OFFER 是套餐表" in p
    assert "业务文档摘要" in p


def test_dim_focus_differs():
    m = build_rerank_prompt("method", business_intent="x", matched_capability="y",
                            candidates_block="c")
    s = build_rerank_prompt("sql", business_intent="x", matched_capability="y",
                            candidates_block="c")
    c = build_rerank_prompt("config", business_intent="x", matched_capability="y",
                            candidates_block="c")
    assert m != s          # 各维 focus 不同
    # 锚到 design §5 三维 focus 关键词(防 focus 文本被改串维而 m!=s 仍通过)
    assert "调用链" in m
    assert "写入侧" in s
    assert "bind_strategy" in c


def test_config_prompt_states_no_raw_value():
    """敏感值脱敏 + §7 双保险 prompt 层: config 维 prompt 必须明示『没有配置原始值』。
    这是 prompt 层唯一承载该保护的子句, 删了红线静默回退 —— 必须有自动守护。"""
    p = build_rerank_prompt("config", business_intent="x", matched_capability="y",
                            candidates_block="c")
    assert "配置原始值" in p


def test_config_prompt_includes_rag_summary_when_given():
    # design §5.1 点 3: sql AND config 维都带 RAG 业务摘要(不只 sql)
    # 注: "业务文档摘要" 也出现在 focus 文本里, 故用 section header "帮你判业务域" + content 当真凭据
    p = build_rerank_prompt("config", business_intent="x", matched_capability="y",
                            candidates_block="[0] ...", rag_summary="参数表描述")
    assert "帮你判业务域" in p          # RAG section header(只在拼了摘要时出现)
    assert "参数表描述" in p            # 摘要 content 真进了 prompt


def test_method_prompt_drops_rag_even_if_given():
    """fail-safe: 即便调用方误给 method 维传 rag_summary, 也不拼进去(§5.1 点 3 结构性兜底)。"""
    p = build_rerank_prompt("method", business_intent="x", matched_capability="y",
                            candidates_block="[0] ...", rag_summary="不该出现的摘要")
    assert "业务文档摘要" not in p
    assert "不该出现的摘要" not in p


def test_empty_anchors_fall_back():
    # business_intent='' -> (未提供); matched_capability='' -> (未分类)
    p = build_rerank_prompt("method", business_intent="", matched_capability="",
                            candidates_block="c")
    assert "(未提供)" in p
    assert "(未分类)" in p


def test_whitespace_only_rag_summary_adds_no_section():
    # sql focus 文本本就含"业务文档摘要", 故只能用 section header "帮你判业务域" 判 section 没拼
    p = build_rerank_prompt("sql", business_intent="x", matched_capability="y",
                            candidates_block="c", rag_summary="   ")
    assert "帮你判业务域" not in p
