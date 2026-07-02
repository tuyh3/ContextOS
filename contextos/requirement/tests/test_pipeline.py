"""pipeline 编排测试(design 02 §7)。

测试思路:
  - test_breakdown_end_to_end_text: 正常路径(ok),三道 guard 全过,字段齐全
  - test_breakdown_requirement_id_deterministic: 同输入产同 requirement_id(sha1 稳定)
  - test_breakdown_explicit_requirement_id: 显式传 requirement_id 时直接用,不派生
  - test_breakdown_empty_input_short_circuits_no_llm_call: 空/空白文本短路,不调 LLM,assessment=rejected
  - test_breakdown_llm_extract_failure_degrades_gracefully: extract 抛 LLMError 降级 + 正则兜底,assessment=degraded
  - test_breakdown_docx_source: docx 路径输入,adapter 解析,pipeline 完整跑通
  - test_breakdown_reject_via_prefilter_no_llm_call: 预筛拦截,0 token 早退,assessment=rejected
  - test_breakdown_reject_via_scope_judge_early_exit: scope judge 判非需求,仅调 1 次 LLM,早退
  - test_breakdown_ok_path_all_grounded: 所有候选 grounded,assessment=ok,confidence=1.0
  - test_breakdown_degraded_via_low_grounding: 脑补候选被 grounding 砍,coverage 低,assessment=degraded
  - test_breakdown_scope_fail_open_to_degraded: scope judge 反复坏 JSON,fail-open,最终 degraded
  - test_breakdown_empty_input_rejected_no_llm: 空文本,无 LLM 调用,assessment=rejected

评分标准:
  - 正常路径字段齐全,candidates 合并正确(LLM + regex 不重复)
  - 空文本短路:llm.calls 必须为空
  - 降级路径:open_questions 有"抽取"关键词,code_names 仍有正则基线
  - requirement_id 前缀 req- + 8 字符 hex(sha1 截取)
  - dict_hits v1 全空列表
  - Guard 1a 预筛早退:0 次 LLM 调用
  - Guard 1b scope judge 早退:1 次 LLM 调用(只 scope judge,不走 extract)
  - grounding 覆盖率低:assessment=degraded,confidence<0.8
  - scope judge fail-open:assessment=degraded 而非 rejected
"""
from __future__ import annotations

import json

from contextos.llm import FakeLLM
from contextos.requirement import RequirementBreakdown, breakdown


def _cls() -> str:
    return json.dumps({"matched_capabilities": [
        {"capability": "billing-charging", "confidence": 0.9, "evidence": "动态计费"}]},
        ensure_ascii=False)


def _tr() -> str:
    return json.dumps({"zh": "新增动态计费批量操作", "en": "Add bulk dynamic charging"},
                      ensure_ascii=False)


def _scope(is_req: bool) -> str:
    # 02b 修订: scope judge 三档 verdict(in_scope/out_of_scope/unsure); True=in_scope
    verdict = "in_scope" if is_req else "out_of_scope"
    return json.dumps({"verdict": verdict, "reason": "x"}, ensure_ascii=False)


def _ext_grounded() -> str:
    # 候选 source_span 都落在 raw_text "新增动态计费批量操作,完成后发短信" 内
    return json.dumps({
        "business_intent": "新增动态计费批量操作",
        "key_entities": ["动态计费"],
        "actions": ["add"],
        "candidate_code_names": [
            {"term": "DynamicCharging", "kind": "camelcase", "source": "llm",
             "source_span": "动态计费"}],
        "candidate_table_terms": [
            {"term": "BILLING", "kind": "entity", "source": "llm", "source_span": "计费"}],
        "candidate_config_keys": [
            {"term": "批量上限", "kind": "param_term", "source": "llm", "source_span": "批量"}],
    }, ensure_ascii=False)


def _ext_hallucinated() -> str:
    # 候选 source_span 都不在 raw_text 内 -> 全被 grounding 砍 -> coverage 低
    return json.dumps({
        "business_intent": "查询促销档期",
        "key_entities": ["促销"],
        "actions": ["modify"],
        "candidate_code_names": [
            {"term": "DiscountPeriod", "kind": "camelcase", "source": "llm",
             "source_span": "促销档期"}],
        "candidate_table_terms": [
            {"term": "PROMOTION", "kind": "entity", "source": "llm", "source_span": "促销表"}],
        "candidate_config_keys": [],
    }, ensure_ascii=False)


def test_breakdown_end_to_end_text():
    # 队列 = scope -> extract -> classify -> translate
    llm = FakeLLM(responses=[_scope(True), _ext_grounded(), _cls(), _tr()])
    b = breakdown("新增动态计费批量操作,完成后发短信", "text", llm=llm)
    assert isinstance(b, RequirementBreakdown)
    assert b.source_kind == "text"
    assert b.business_intent == "新增动态计费批量操作"
    assert b.actions == ["add"]
    assert any(c.capability == "billing-charging" for c in b.matched_capabilities)
    assert any(t.term == "BILLING" for t in b.candidate_table_terms)  # grounded(span "计费"在原文)
    assert b.queries.en == "Add bulk dynamic charging"
    assert b.dict_hits.interface_dict == []
    assert b.requirement_id.startswith("req-")
    assert b.assessment == "ok"


def test_breakdown_requirement_id_deterministic():
    llm1 = FakeLLM(responses=[_scope(True), _ext_grounded(), _cls(), _tr()])
    llm2 = FakeLLM(responses=[_scope(True), _ext_grounded(), _cls(), _tr()])
    b1 = breakdown("新增动态计费批量操作", "text", llm=llm1)
    b2 = breakdown("新增动态计费批量操作", "text", llm=llm2)
    assert b1.requirement_id == b2.requirement_id


def test_breakdown_explicit_requirement_id():
    llm = FakeLLM(responses=[_scope(True), _ext_grounded(), _cls(), _tr()])
    b = breakdown("新增动态计费批量操作", "text", llm=llm, requirement_id="REQ-CUSTOM-1")
    assert b.requirement_id == "REQ-CUSTOM-1"


def test_breakdown_empty_input_short_circuits_no_llm_call():
    llm = FakeLLM(responses=[])   # 不该被调
    b = breakdown("   ", "text", llm=llm)
    assert b.raw_text == ""
    assert b.assessment == "rejected"
    assert b.open_questions and "解析失败" in b.open_questions[0]
    assert llm.calls == []


def test_breakdown_llm_extract_failure_degrades_gracefully():
    """extract 反复产坏 JSON -> 该步降级 + 正则兜底;assessment=degraded。"""
    llm = FakeLLM(responses=[_scope(True), "not json", "still bad", "nope", _cls(), _tr()])
    b = breakdown("新增 Dynamic Charging 需求支持", "text", llm=llm)
    assert b.business_intent == ""
    assert any("抽取" in q for q in b.open_questions)
    assert any(c.term == "DynamicCharging" for c in b.candidate_code_names)  # 正则基线豁免
    assert b.assessment == "degraded"


def test_breakdown_docx_source(make_docx):
    llm = FakeLLM(responses=[_scope(True), _ext_grounded(), _cls(), _tr()])
    path = make_docx(paragraphs=["新增动态计费批量操作,完成后发短信"],
                     table_rows=[["字段", "说明"], ["OFFER_ID", "套餐标识"]])
    b = breakdown(str(path), "docx", llm=llm)
    assert b.source_kind == "docx"
    assert "OFFER_ID | 套餐标识" in b.raw_text


def test_breakdown_reject_via_prefilter_no_llm_call():
    llm = FakeLLM(responses=[])      # 一次都不该调
    b = breakdown("9.9-9.11=?", "text", llm=llm)
    assert b.assessment == "rejected"
    assert b.confidence == 0.0
    assert llm.calls == []           # 预筛 0 token 早退
    assert b.candidate_code_names == []


def test_breakdown_reject_via_scope_judge_early_exit():
    # 绕过预筛(够长 + 有字母), 但 scope judge 判非需求
    llm = FakeLLM(responses=[_scope(False)])
    b = breakdown("please compute the integral of x squared dx now", "text", llm=llm)
    assert b.assessment == "rejected"
    assert len(llm.calls) == 1       # 只调了 scope judge, extract/classify/translate 未被调
    assert b.business_intent == ""


def test_breakdown_ok_path_all_grounded():
    llm = FakeLLM(responses=[_scope(True), _ext_grounded(), _cls(), _tr()])
    b = breakdown("新增动态计费批量操作,完成后发短信", "text", llm=llm)
    assert b.assessment == "ok"
    assert b.confidence == 1.0        # scope 1.0 x grounding 1.0
    assert any(c.term == "DynamicCharging" for c in b.candidate_code_names)
    # 所有候选都带 source_span
    for c in b.candidate_code_names + b.candidate_table_terms + b.candidate_config_keys:
        assert c.source_span


def test_breakdown_degraded_via_low_grounding():
    llm = FakeLLM(responses=[_scope(True), _ext_hallucinated(), _cls(), _tr()])
    b = breakdown("新增动态计费批量操作,完成后发短信", "text", llm=llm)
    assert b.assessment == "degraded"
    assert b.confidence < 0.8          # 脑补候选被砍 -> coverage 低
    assert any("grounding" in q or "脑补" in q for q in b.open_questions)


def test_breakdown_scope_fail_open_to_degraded():
    # scope judge 反复坏 JSON(3 次)-> fail-open -> 不 REJECT, 放行走 extract, 最终 DEGRADED
    llm = FakeLLM(responses=["bad", "bad", "bad", _ext_grounded(), _cls(), _tr()])
    b = breakdown("新增动态计费批量操作,完成后发短信", "text", llm=llm)
    assert b.assessment == "degraded"
    assert any("scope" in q for q in b.open_questions)


# (空文本 REJECT 由 test_breakdown_empty_input_short_circuits_no_llm_call 覆盖, 不重复)


# --- Task 8: 切分门槛 + 逐组 extract + 跨段合并 + 分段低置信 ---

from contextos.llm import LLMError  # noqa: E402
from contextos.requirement.extract import ExtractionResult  # noqa: E402
from contextos.requirement.pipeline import breakdown as _breakdown_for_seg  # noqa: E402
from contextos.requirement.schema import CandidateName  # noqa: E402
from contextos.requirement.segmentation import segment  # noqa: E402


def _aux_handler(prompt: str, system: str | None) -> str:
    """scope / classify / translate 的正确形状(extract 由测试 monkeypatch 掉, 不走这里)。
    形状对真契约: scope.py -> {verdict}; classifier.py -> {matched_capabilities}; translate -> {zh,en}。
    """
    if "matched_capabilities" in prompt:
        return json.dumps({"matched_capabilities": []})
    if "双语" in prompt or '"zh"' in prompt:
        return json.dumps({"zh": "", "en": ""})
    return json.dumps({"verdict": "in_scope"})


def test_breakdown_segments_long_structured_input_per_segment(monkeypatch):
    """长结构化输入 -> extract 被多次调用, 不同段正文分别喂进去(证明分段生效)。
    extract 用 stub 屏蔽 grounding/schema 干扰, 只验"分段后逐组喂"这一契约。
    """
    # 尾部填充需让 estimate_tokens(raw) > 800 预算才触发 should_segment(chars/2.5)
    raw = ("X 总览\n" + "1. 配置开关说明文字一二三四五六七八\n"
           "    a. 子项一相关说明文字一二三四五\n"
           "    b. 子项二相关说明文字一二三四五\n") + ("尾部填充" * 600)
    seen: list[str] = []

    def stub_extract(llm, raw_text, *, context_path="", stop_keywords_path=None):
        seen.append(raw_text)
        return ExtractionResult(business_intent="i", key_entities=[], actions=[],
                                candidate_code_names=[], candidate_table_terms=[],
                                candidate_config_keys=[])

    monkeypatch.setattr("contextos.requirement.pipeline.extract", stub_extract)
    _breakdown_for_seg(raw, "text", llm=FakeLLM(handler=_aux_handler))
    assert len(seen) >= 2                              # 多段 -> 多次 extract
    joined = "\n".join(seen)
    assert "子项一" in joined and "子项二" in joined    # 深层段被喂到


def test_breakdown_short_input_single_pass(monkeypatch):
    """短输入不进切分(零额外开销): extract 只被调一次。"""
    calls = {"n": 0}

    def stub_extract(llm, raw_text, *, context_path="", stop_keywords_path=None):
        calls["n"] += 1
        return ExtractionResult(business_intent="i", key_entities=[], actions=[],
                                candidate_code_names=[], candidate_table_terms=[],
                                candidate_config_keys=[])

    monkeypatch.setattr("contextos.requirement.pipeline.extract", stub_extract)
    _breakdown_for_seg("1. 一句话需求", "text", llm=FakeLLM(handler=_aux_handler))
    assert calls["n"] == 1


# --- 降级路径回归(原仅 ad-hoc 探针, 现固化): 全组失败 / 部分失败 / 分段低置信 ---
# 三条路径共用切分门槛: raw 需 estimate_tokens > 800 才进 should_segment(尾部填充 * 600)。
# degraded 标志的真实字段 = RequirementBreakdown.assessment(取值 "degraded"); 见 schema.py:113。
# 三条 open_question 原文逐字(pipeline.py:136/140/159):
#   全组   -> "LLM 抽取全组降级, 仅正则基线种子可用"
#   部分   -> "LLM 抽取 {n}/{total} 组降级"
#   分段低 -> "本需求分段置信低, 可能漏召回"


def test_breakdown_all_groups_fail_degrades_to_regex_baseline(monkeypatch):
    """全组 extract 失败 -> 退正则基线兜底, 不抛异常, assessment=degraded。

    设计思路: 长结构化输入切成 >=2 组, stub extract 每组都抛真 LLMError;
              逐组 try 全 catch 后 n_fail == len(groups) -> 走 pipeline.py:134 全组分支。
              raw 内放 SCREAMING_CASE 标识符 FTTH_FLAG(纯中文填充不产正则命中), 验
              "LLM 全降级仍有正则种子"这一兜底契约。
    评分标准: (1) breakdown 不抛; (2) assessment=="degraded"; (3) open_questions 含
              "全组降级"原文; (4) 正则基线种子 FTTH_FLAG 存活到 candidate_code_names。
    脚本逻辑: monkeypatch pipeline.extract 为恒抛 LLMError 的 stub; FakeLLM(handler=_aux_handler)
              供 scope/classify/translate; 断言上述四点。
    """
    raw = ("X 总览 FTTH_FLAG 开关\n"
           "1. 配置开关说明文字一二三四五六七八\n"
           "    a. 子项一相关说明文字一二三四五\n"
           "    b. 子项二相关说明文字一二三四五\n") + ("尾部填充" * 600)

    def stub_extract(llm, raw_text, *, context_path="", stop_keywords_path=None):
        raise LLMError("extract boom")

    monkeypatch.setattr("contextos.requirement.pipeline.extract", stub_extract)
    b = _breakdown_for_seg(raw, "text", llm=FakeLLM(handler=_aux_handler))
    assert b.assessment == "degraded"
    assert any("全组降级" in q for q in b.open_questions)
    # 正则基线豁免 LLM: FTTH_FLAG 被 _regex_baseline 命中, grounding 也留存(span 在原文)
    assert any(c.term == "FTTH_FLAG" for c in b.candidate_code_names)
    assert b.candidate_code_names  # 至少一个种子, 没被全砍


def test_breakdown_partial_group_fail_keeps_survivors(monkeypatch):
    """首组 extract 失败 / 其余成功 -> 部分降级, 存活组候选不被丢。

    设计思路: 长结构化输入切成 >=2 组; stub extract 第一次调用抛 LLMError, 后续返回带
              SurvivorTerm 的合法 ExtractionResult。0 < n_fail < len(groups) -> 走
              pipeline.py:138 部分分支, 且成功组的合并候选保留(MEDIUM 4 单组失败不拖垮其余)。
    评分标准: (1) assessment=="degraded"; (2) open_questions 命中 "n/total 组降级" 形状
              (含 "组降级" 且带 "/"); (3) 存活组的 SurvivorTerm 候选进入最终 candidate_code_names。
    脚本逻辑: 计数器 stub: 第一次 raise, 之后 return ExtractionResult(候选含
              CandidateName(term="SurvivorTerm", source_span="SurvivorTerm"));
              raw 内嵌字面 "SurvivorTerm" 让 grounding 子串核验通过。
    """
    raw = ("X 总览 SurvivorTerm 段\n"
           "1. 配置开关说明文字一二三四五六七八 SurvivorTerm\n"
           "    a. 子项一相关说明文字一二三四五\n"
           "    b. 子项二相关说明文字一二三四五\n") + ("尾部填充" * 600)
    calls = {"n": 0}

    def stub_extract(llm, raw_text, *, context_path="", stop_keywords_path=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMError("first group boom")
        return ExtractionResult(
            business_intent="幸存组意图", key_entities=[], actions=[],
            candidate_code_names=[CandidateName(
                term="SurvivorTerm", kind="other", source="llm",
                source_span="SurvivorTerm")],
            candidate_table_terms=[], candidate_config_keys=[])

    monkeypatch.setattr("contextos.requirement.pipeline.extract", stub_extract)
    b = _breakdown_for_seg(raw, "text", llm=FakeLLM(handler=_aux_handler))
    assert calls["n"] >= 2                                    # 确实有失败 + 成功两类调用
    assert b.assessment == "degraded"
    assert any("组降级" in q and "/" in q for q in b.open_questions)
    assert any(c.term == "SurvivorTerm" for c in b.candidate_code_names)


def test_breakdown_low_confidence_segmentation_degrades(monkeypatch):
    """分段多数低置信 -> 即便每组 extract 全成功, 仍因 seg_low 独立降级。

    设计思路: 构造检测器产出"多数 low 置信"标记的输入(reviewer 实证: 5)/9)/3) 这种
              带分隔符的跳号/乱序 arabic, 2 低 1 高 of 3 -> 2 > max(1, 3//2)=1 触发 seg_low,
              见 pipeline.py:114/157)。extract 每组都成功, 证明 seg_low 这条路径独立于
              抽取成败也能把结果打到 degraded。
    评分标准: (1) assessment=="degraded"; (2) open_questions 含 "分段置信低" 原文;
              (3) 前置事实: [s.confidence for s in segment(raw) if s.level>0] 多数为 low。
    脚本逻辑: 先用真 segment() 断言多数 low(防"构造的输入其实不触发"假绿);
              再 monkeypatch extract 为恒成功 stub, 跑 breakdown 验降级。
    判别性(实测): 把 9) 改成不跳号的合法后继会让 2 个 low 翻成 high, seg_low 不再触发,
              该测试会失败 -- 即此断言确实绑在低置信多数这一条件上。
    """
    raw = ("5) DynamicCharging 开关说明文字一二三四五六七八\n"
           "9) FTTH_FLAG 跳号太多说明文字一二三四五六\n"
           "3) 回退序号说明文字一二三四五六七八\n") + ("尾部填充" * 600)

    # 前置: 确认这条输入的标记确实多数低置信(否则后面的降级断言无判别力)
    confs = [s.confidence for s in segment(raw) if s.level > 0]
    n_low = sum(1 for c in confs if c == "low")
    assert n_low > max(1, len(confs) // 2), f"输入未触发分段低置信多数: {confs}"

    def stub_extract(llm, raw_text, *, context_path="", stop_keywords_path=None):
        return ExtractionResult(business_intent="i", key_entities=[], actions=[],
                                candidate_code_names=[], candidate_table_terms=[],
                                candidate_config_keys=[])

    monkeypatch.setattr("contextos.requirement.pipeline.extract", stub_extract)
    b = _breakdown_for_seg(raw, "text", llm=FakeLLM(handler=_aux_handler))
    assert b.assessment == "degraded"                        # 抽取全成功仍降级
    assert any("分段置信低" in q for q in b.open_questions)


# --- Task 5: pipeline 三入口串接(breakdown 级)- P1a 漏点回归 ---
# 三入口 = pipeline.py:107 直接正则基线 / :120 分段逐组 extract / :146 非分段 extract。
# 缺任一入口未串 cfg.stop_keywords_path -> 客户配了停用词表, 该入口仍不过滤(behavior 不 byte-identical)。


def _fail_llm():
    return FakeLLM(handler=lambda prompt, system: "not json")   # 恒坏 JSON -> LLM 全降级


def _profile_with_stop(tmp_path, cust_path=""):
    from contextos.profile.schema import Profile
    return Profile(**{
        "llm": {"provider": "fake", "api_key_env": "K"},
        "embedding": {"model": "BAAI/bge-m3"},
        "reranker": {"enabled": True, "model": "x", "top_k_input": 50, "top_k_output": 10},
        "query_expansion": {"enabled": True, "translation_provider": "main_llm", "fallback_provider": "x"},
        "storage": {"data_dir": str(tmp_path)},
        "ingestion": {"default_cleanup": "full", "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "/j", "lombok_path": "/l", "java_home": "/h"},
        "oracle": {"tns_admin": "/t", "allowed_instances": ["TEST_DB1"]},
        "input": {"scope": {"stop_keywords_path": cust_path}},
        "projects": [{"name": "demoproj", "path": "/p", "language": "java", "build_system": "gradle"}],
    })


def test_pipeline_line107_filters_customer_stop(tmp_path):
    """LLM 全降级时 code_names 退 pipeline.py:107 正则基线; 配客户文件则客户词被滤(P1a 漏点回归)。"""
    cust = tmp_path / "cust.txt"
    cust.write_text("FOOSVC\n", encoding="utf-8")
    text = ("Add FOOSVC to the Dynamic Charging flow so operators configure it end to end")
    r_no = breakdown(text, "text", llm=_fail_llm(), profile=_profile_with_stop(tmp_path, ""))
    r_cust = breakdown(text, "text", llm=_fail_llm(), profile=_profile_with_stop(tmp_path, str(cust)))
    no_terms = {c.term for c in r_no.candidate_code_names}
    cust_terms = {c.term for c in r_cust.candidate_code_names}
    assert "FOOSVC" in no_terms                # 不配: 通用 default 不含 FOOSVC -> 保留
    assert "FOOSVC" not in cust_terms          # 配客户文件 -> 被滤
    assert "DynamicCharging" in cust_terms      # 真业务词不受影响(空格输入 -> 粘连输出)


def test_pipeline_segmented_line107_filters_customer_stop(tmp_path):
    """分段路径: 全组 LLM 失败 -> 退 pipeline.py:107 正则基线(同一漏点, 分段入口)。"""
    from contextos.requirement.segmentation import should_segment   # 真实模块名 segmentation
    cust = tmp_path / "cust.txt"
    cust.write_text("FOOSVC\n", encoding="utf-8")
    # 触发分段需 estimate_tokens(raw) > 800(grouping.py:19); 照 test_pipeline.py 的
    # "尾部填充" * 600 长文本模式撑过预算, 把客户词/业务词嵌进小标题子项。
    text = ("一. 需求主标题\n"
            "    a. Add FOOSVC feature here 说明文字一二三四五\n"
            "    b. Modify Dynamic Charging here 说明文字一二三四五\n") + ("尾部填充" * 600)
    assert should_segment(text)   # 确定性触发, 非"调整直到"
    r = breakdown(text, "text", llm=_fail_llm(), profile=_profile_with_stop(tmp_path, str(cust)))
    assert "FOOSVC" not in {c.term for c in r.candidate_code_names}
