"""tracked 中性 + 结构探针 gate: ops-localization skill 文件不得含真客户包名, 且结构 token 齐全。

设计思路: 复用 requirement-impact-analysis 的中性口径(com.<corp>. pkg 正则 + gitignored denylist),
加 ops 专属结构探针(五门 / 五类枚举 / 三态 / 假设表块 / abstain / 跨 skill 引用)防 skill 被静默抽空。
评分标准: 任何非 allowlist 的 com.<word>. -> 判泄漏; 缺任一结构 token -> 判抽空。
脚本逻辑: glob ops skill md -> 跑 regex + token 清单 -> 收集违例(文件:命中 / 缺失 token)。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# repo 根 = 本测试文件向上 3 层(tests -> mcp_server -> contextos -> repo)
_REPO = Path(__file__).resolve().parents[3]
_SKILL_DIR = _REPO / ".claude" / "skills" / "ops-localization"
_SKILL = _SKILL_DIR / "SKILL.md"
_BEHAVIOR_REF = _SKILL_DIR / "references" / "行为类别与dontmiss速查.md"
_OUT_TMPL = _SKILL_DIR / "references" / "ops输出模板.md"

# 允许的合成占位包名第二段(与 requirement skill 同口径)
_ALLOWED = {"example", "x"}
_PKG_RE = re.compile(r"com\.([a-z][a-zA-Z0-9_]*)\.")

# 真样本代号清单(gitignored,本地有 / CI 无),复用现 skill 同一份
_DENYLIST = _REPO / "database" / "requirement-skill-denylist.txt"


def _violations(text: str) -> list[str]:
    return [m.group(0) for m in _PKG_RE.finditer(text) if m.group(1) not in _ALLOWED]


def test_regex_catches_a_real_looking_package() -> None:
    # 自检: 探针必须能抓到非 allowlist 的 com.<corp>. 包名
    assert _violations("import com.realcorp.order.Foo;") == ["com.realcorp."]
    assert _violations("com.example.app.X / com.x.y.Z") == []


def test_ops_skill_files_exist() -> None:
    assert _SKILL.exists(), f"缺 SKILL.md: {_SKILL}"
    assert _BEHAVIOR_REF.exists(), f"缺行为类别速查: {_BEHAVIOR_REF}"
    assert _OUT_TMPL.exists(), f"缺 ops 输出模板: {_OUT_TMPL}"


def test_ops_skill_files_have_no_real_package_names() -> None:
    md_files = sorted(_SKILL_DIR.rglob("*.md"))
    assert md_files, f"no skill md under {_SKILL_DIR}"
    offenders = {}
    for f in md_files:
        v = _violations(f.read_text(encoding="utf-8"))
        if v:
            offenders[str(f.relative_to(_REPO))] = sorted(set(v))
    assert not offenders, f"non-neutral package names in ops skill files: {offenders}"


# SKILL.md 必含结构 token(缺任一 = 脊柱被抽空)。中性、与具体客户无关。
_SKILL_REQUIRED = [
    "name: ops-localization",
    "Phase 0", "Phase 1", "Phase 2", "Phase 3", "Phase 4", "Phase 5",
    "门①", "门②", "门③", "门④", "门⑤",
    "可达性不定排序",                  # 守则一
    "inconclusive 不等于低可能性",      # 守则二
    "业务因果先验优先于代码可证性",      # 守则三
    "select-not-generate",            # 守则四
    "human-gated",                    # 守则五
    "record_confirmed_case",          # Phase 5 回写(组件 B 签名引用)
    "don't-miss",
    "behavior_class",
    "abstain",
    "tmp/ops-localization",           # gitignored 落点
    "drill-loop速查.md",              # 跨 skill 引用(不复制)
    "contextos-已知gap.md",           # 跨 skill 引用(不复制)
    "行为类别与dontmiss速查.md",       # 本 skill 专属 ref
    "ops输出模板.md",                 # 本 skill 专属 ref
]


def test_skill_has_required_skeleton() -> None:
    text = _SKILL.read_text(encoding="utf-8")
    missing = [t for t in _SKILL_REQUIRED if t not in text]
    assert not missing, f"SKILL.md 脊柱缺结构 token: {missing}"


def test_skill_cross_references_not_copied() -> None:
    """跨 skill 引用不复制(spec §5):drill-loop / 已知gap 用 ../requirement-impact-analysis 路径引,
    不在 ops 目录复制同名文件。"""
    text = _SKILL.read_text(encoding="utf-8")
    assert "../requirement-impact-analysis/references/drill-loop速查.md" in text, \
        "SKILL.md 未用相对路径跨引 drill-loop 速查(应引用不复制)"
    assert not (_SKILL_DIR / "references" / "drill-loop速查.md").exists(), \
        "ops 目录复制了 drill-loop 速查(应跨 skill 引用,不复制)"
    assert not (_SKILL_DIR / "references" / "contextos-已知gap.md").exists(), \
        "ops 目录复制了 已知gap(应跨 skill 引用,不复制)"


# 行为类别速查必含 token
_BEHAVIOR_REQUIRED = [
    "behavior_class", "扣费", "资格", "配置", "数据状态", "时序",
    "textbook signature", "时点矩阵", "mechanism_tag",
    "四形态", "召回容错",
]


def test_behavior_ref_has_required_sections() -> None:
    text = _BEHAVIOR_REF.read_text(encoding="utf-8")
    missing = [t for t in _BEHAVIOR_REQUIRED if t not in text]
    assert not missing, f"行为类别速查缺结构 token: {missing}"


# ops 输出模板必含 token(三态 + 排序 + abstain + 落点 + worked example)
_OUT_REQUIRED = [
    "差异化根因假设表", "validated", "invalidated", "inconclusive",
    "业务因果排序", "abstain", "机制族", "时点矩阵",
    "tmp/ops-localization", "Worked example",
]


def test_output_template_has_required_sections() -> None:
    text = _OUT_TMPL.read_text(encoding="utf-8")
    missing = [t for t in _OUT_REQUIRED if t not in text]
    assert not missing, f"ops 输出模板缺结构 token: {missing}"
    # worked example 中性(master-data 风格 APP_ 表名 + com.example)
    assert "com.example" in text, "ops 输出模板 worked example 缺中性 FQN(com.example)"


def test_no_sample_codename_in_ops_skill() -> None:
    """pushed 的 ops skill 目录不得含 gitignored 代号清单里的裸代号(连 com.<corp>. 抓不到的真表名/类名也拦)。
    清单 gitignored -> 缺失时 skip(CI-safe);本地存在时机械扫,命中即 fail。"""
    if not _DENYLIST.exists():
        pytest.skip(f"代号清单 gitignored 缺失({_DENYLIST}):本地跑才扫,CI 由人工兜底")
    raw = _DENYLIST.read_text(encoding="utf-8").splitlines()
    terms = [t.strip() for t in raw if t.strip() and not t.strip().startswith("#") and len(t.strip()) >= 3]
    assert terms, f"代号清单为空: {_DENYLIST}(fail-closed)"
    offenders: dict[str, list[str]] = {}
    for md in sorted(_SKILL_DIR.rglob("*.md")):
        low = md.read_text(encoding="utf-8").lower()
        hits = [t for t in terms if t.lower() in low]
        if hits:
            offenders[str(md.relative_to(_REPO))] = hits
    assert not offenders, f"ops tracked 文档命中样本代号(应只落 gitignored): {offenders}"


# --- Phase 1A 症状阶段 checkpoint(spec 2026-06-30)---
# 缺任一 token = 门⓪被抽空; 位置测试守"路由后移于暂停之后"。
_PHASE1A_SKILL_REQUIRED = [
    "Phase 1A",
    "症状阶段表征",
    "turn boundary",
    "resume",
    "OPS_LOCALIZATION_CHECKPOINT",
    "awaiting_user_confirmation",
    "assumed_unconfirmed",
    "assumed_with_user_correction",
    "cancelled",
    "checkpoint_id",
    "resume_next",
    "严禁从代码符号名",                       # 反推铁律(根因修复本体)
    "not_provided",                          # 爆炸半径子项 pending(取代旧哨兵值)
    "不得静默改阶段",
    "fail-closed",                           # 多 awaiting 报告
]


def test_skill_has_phase1a_gate() -> None:
    text = _SKILL.read_text(encoding="utf-8")
    missing = [t for t in _PHASE1A_SKILL_REQUIRED if t not in text]
    assert not missing, f"SKILL.md 缺 Phase 1A 门⓪ 结构 token: {missing}"


def test_phase1a_before_behavior_class_routing() -> None:
    # 位置铁律(spec 附录 B): Phase 1A(症状阶段表征/单暂停)必须在 Phase 1B(behavior_class 路由)之前
    text = _SKILL.read_text(encoding="utf-8")
    i_1a = text.find("### Phase 1A")
    i_1b = text.find("### Phase 1B")
    assert i_1a != -1, "SKILL.md 缺 Phase 1A 段"
    assert i_1b != -1, "SKILL.md 缺 Phase 1B(behavior_class 路由后移)段"
    assert i_1a < i_1b, "位置铁律违反: behavior_class 路由(Phase 1B)未在 Phase 1A 之后"
    assert "behavior_class" in text[i_1b:i_1b + 400], "Phase 1B 段未承载 behavior_class 路由"


# --- §7 阶段探针 + 鉴别问(spec 附录 F)---
_PROBE_REQUIRED = [
    "够不到", "看不到", "进不下去", "被拒", "结果错",   # generic 阶段探针词汇
    "阶段探针",
    "鉴别问",                                          # 中性鉴别问清单
    "正常兄弟对照",                                     # 只 X 还是全部 / 兄弟对照
]


def test_behavior_ref_has_stage_probe_section() -> None:
    text = _BEHAVIOR_REF.read_text(encoding="utf-8")
    missing = [t for t in _PROBE_REQUIRED if t not in text]
    assert not missing, f"行为类别速查缺 §7 阶段探针/鉴别问 token: {missing}"


# --- OPS_LOCALIZATION_CHECKPOINT 块 + 报告头渲染(spec 附录 C/F)---
_CHECKPOINT_FIELDS = [
    "checkpoint_id", "report_path", "phase", "status", "stage_assumption",
    "business_wording", "blast_radius", "excluded_stages", "question", "resume_next",
]


def test_output_template_has_checkpoint_block() -> None:
    text = _OUT_TMPL.read_text(encoding="utf-8")
    assert "OPS_LOCALIZATION_CHECKPOINT" in text, "ops 输出模板缺 checkpoint 块"
    missing = [f for f in _CHECKPOINT_FIELDS if f not in text]
    assert not missing, f"checkpoint 块缺字段(应 10 个齐): {missing}"


def test_output_template_renders_phase1a_in_header() -> None:
    text = _OUT_TMPL.read_text(encoding="utf-8")
    for t in ["Phase 1A", "stage_assumption", "assumed_unconfirmed"]:
        assert t in text, f"ops 输出模板报告头未渲染 Phase 1A 项: {t}"


# --- 契约测试(非 token 探针; ChatGPT review 2026-06-30 Finding 2/3/6)---
# token 探针只查"在不在/位置对不对", 抓不到"契约对不对"。以下两条验真契约:
# (1) worked example 教的 class/tag 组合必须能过 record_confirmed_case 的枚举校验(否则教 host 写 reject)。
# (2) behavior_class 路由已移 Phase 1B, 旧"Phase 1 路由"框架不得残留(自相矛盾 token 都在、位置测试抓不到)。


def test_output_template_worked_example_pairs_valid_in_enum() -> None:
    """worked example 每个 `<class> / <tag>` 对必须 ∈ MECHANISM_TAGS 且 class 匹配。
    否则 host 照抄会得到 record_confirmed_case reject 的非法组合(Finding 2:
    原 `资格`/`order_no_balance_gate` 是 `扣费` tag; `config_default_fallback` 根本不在枚举)。"""
    from contextos.ops.mechanism_tags import MECHANISM_TAGS

    text = _OUT_TMPL.read_text(encoding="utf-8")
    pairs = re.findall(
        r"behavior_class \+ mechanism_tag\*\*[:：]\s*`([^`]+)`\s*/\s*`([^`]+)`", text
    )
    assert len(pairs) >= 3, f"worked example class/tag 对解析数异常(应 >=3): {pairs}"
    bad = []
    for cls, tag in pairs:
        if tag not in MECHANISM_TAGS:
            bad.append(f"未知 tag `{tag}`(不在 MECHANISM_TAGS)")
        elif MECHANISM_TAGS[tag] != cls:
            bad.append(f"class/tag 不一致 `{cls}`/`{tag}`(枚举属 `{MECHANISM_TAGS[tag]}`)")
    assert not bad, f"worked example 教了非法组合(record_confirmed_case 会 reject): {bad}"


def test_skill_no_old_phase1_routing_residue() -> None:
    """behavior_class 路由已移到 Phase 1B, 旧'Phase 1 路由'框架不得残留(Finding 3)。
    位置探针只查 1A<1B, 抓不到'门落点/自检/Phase 1 段仍按旧流程'的自相矛盾。"""
    text = _SKILL.read_text(encoding="utf-8")
    # 5 门落点: 门① 指向 Phase 1B
    assert re.search(r"门① 路由\s*->\s*Phase 1B", text), "5 门落点 门① 未指向 Phase 1B"
    assert "门① 路由 -> Phase 1。" not in text, "5 门落点 残留旧 门① -> Phase 1"
    # Phase 1 段(### Phase 1 现象结构化 ... ### Phase 1A)内不得再写 behavior_class 路由
    i_p1 = text.find("### Phase 1 现象结构化")
    i_p1a = text.find("### Phase 1A")
    assert i_p1 != -1 and i_p1a != -1 and i_p1 < i_p1a, "Phase 1 / Phase 1A 段锚缺失"
    seg = text[i_p1:i_p1a]
    assert "behavior_class 五类路由" not in seg, "Phase 1 段内仍残留 behavior_class 五类路由(应在 Phase 1B)"
    assert "+ behavior_class 路由" not in seg, "Phase 1 段 checkpoint 仍写 behavior_class 路由"
    # 自检清单须同时覆盖 Phase 1A 与 Phase 1B
    i_sc = text.find("## 自检清单")
    i_ref = text.find("## references")
    assert i_sc != -1 and i_ref != -1 and i_sc < i_ref, "自检清单 / references 段锚缺失"
    sc = text[i_sc:i_ref]
    assert "Phase 1A" in sc, "自检清单缺 Phase 1A 行"
    assert "Phase 1B" in sc, "自检清单缺 Phase 1B 行"


# --- Spec A 附录 E: resume 协议重写(F1 自举重排 + 抗污染 + incident_signature + 4 终态) ---

def test_resume_order_fixes_f1_bootstrap() -> None:
    """F1 自举悖论修: 'resume-scan 需 data_dir' 必须排在 'profile_info 取 data_dir' 之后,
    不能'先扫目录但不知道目录在哪'。位置感知: 找到两个锚句的下标, 断言顺序。"""
    text = _SKILL.read_text(encoding="utf-8")
    i_start = text.find("### 起手第一步")
    assert i_start != -1, "SKILL.md 缺起手第一步段"
    i_end = text.find("### Phase 0", i_start)
    assert i_end != -1, "SKILL.md 缺 Phase 0 段(起手第一步之后)"
    seg = text[i_start:i_end]
    i_profile = seg.find("profile_info")
    i_scan = seg.find("再扫")
    assert i_profile != -1, "起手第一步段缺 profile_info 引用(F1 修复本体)"
    assert i_scan != -1, "起手第一步段缺'再扫'措辞(F1 修复本体)"
    assert i_profile < i_scan, "F1 未修: profile_info 取 data_dir 必须在扫目录之前"


def test_resume_anti_contamination_present() -> None:
    """抗污染: 只有真答暂停问才 resume; 新工单/泛任务问不 auto-resume;
    incident_signature 机械匹配(签名不符不 resume)。"""
    text = _SKILL.read_text(encoding="utf-8")
    i_start = text.find("### 起手第一步")
    i_end = text.find("### Phase 0", i_start)
    seg = text[i_start:i_end]
    for token in ["抗污染", "按新工单起", "incident_signature", "逐字不等"]:
        assert token in seg, f"起手第一步段缺抗污染 token: {token}"


def test_resume_four_terminal_states_in_skill() -> None:
    """status 四终态(含新增 cancelled)必须在起手第一步段内完整出现。"""
    text = _SKILL.read_text(encoding="utf-8")
    i_start = text.find("### 起手第一步")
    i_end = text.find("### Phase 0", i_start)
    seg = text[i_start:i_end]
    for token in ["confirmed", "assumed_with_user_correction", "assumed_unconfirmed", "cancelled"]:
        assert token in seg, f"起手第一步段缺终态 token: {token}"
    assert "terminal" in seg, "起手第一步段缺 cancelled=terminal 说明"


# --- Spec A 附录 A/B: Phase 1A turn boundary 两窗 + 三上下文 ---

def test_phase1a_turn_boundary_two_windows() -> None:
    """写 checkpoint 前后两窗口: 窗2(写完后)必须明确禁一切工具含 rag_search, 不止 drill。
    位置感知: 定位'窗 2'到下一个'###'标题之间的切片, 断言 rag_search 与禁令同段。"""
    text = _SKILL.read_text(encoding="utf-8")
    i_1a = text.find("### Phase 1A")
    i_1b = text.find("### Phase 1B")
    assert i_1a != -1 and i_1b != -1 and i_1a < i_1b
    seg = text[i_1a:i_1b]
    assert "窗 1" in seg and "窗 2" in seg, "Phase 1A 段缺两窗划分"
    i_w2 = seg.find("窗 2")
    w2_seg = seg[i_w2:]
    assert "rag_search" in w2_seg, "窗2 段未提及 rag_search(应禁一切工具含它, 不止 drill)"
    assert "禁止一切工具调用" in w2_seg, "窗2 段未明确'禁止一切工具调用'"
    assert "Phase 1B/2/3/4" in w2_seg or ("Phase 1B" in w2_seg and "2/3/4" in w2_seg), \
        "窗2 段未明确禁进后续 Phase"


def test_phase1a_three_context_table() -> None:
    """三上下文降级(交互式/subagent/batch-cron), subagent 必须交回父/leader、不自降级。"""
    text = _SKILL.read_text(encoding="utf-8")
    i_1a = text.find("### Phase 1A")
    i_1b = text.find("### Phase 1B")
    seg = text[i_1a:i_1b]
    for token in ["交互式", "subagent", "batch/cron"]:
        assert token in seg, f"Phase 1A 段缺上下文类别: {token}"
    i_subagent = seg.find("subagent")
    subagent_ctx = seg[i_subagent:i_subagent + 200]
    assert "父" in subagent_ctx or "leader" in subagent_ctx, \
        "subagent 行 200 字符窗口内未提'交回父/leader'"
    assert "不得自降级" in seg, "Phase 1A 段缺'subagent 不得自降级'铁律"


def test_phase1a_retired_tokens_absent() -> None:
    """旧'单点硬暂停'标题词与旧 blast_radius 哨兵值 unknown_pending_user_confirmation
    已被 turn boundary / 结构化子块 not_provided 取代, 不应再残留(防止两套措辞并存混淆)。"""
    text = _SKILL.read_text(encoding="utf-8")
    assert "单点硬暂停" not in text, "SKILL.md 仍残留旧'单点硬暂停'标题(应已改 turn boundary)"
    assert "unknown_pending_user_confirmation" not in text, \
        "SKILL.md 仍残留旧 blast_radius 哨兵值(应已改结构化子块 not_provided)"


# --- Spec A 附录 D: Phase 4 pairwise-discriminator + 规模信号反例固化 ---

def test_core_discipline_has_pairwise_abstain_rule() -> None:
    """核心纪律列表新增第 6 条: 缺决定性信号时 pairwise abstain, 不拿弱代理顶替。"""
    text = _SKILL.read_text(encoding="utf-8")
    i_start = text.find("## 核心纪律")
    i_end = text.find("## 自检清单", i_start)
    assert i_start != -1 and i_end != -1
    seg = text[i_start:i_end]
    assert "pairwise" in seg or "假设对" in seg, "核心纪律缺 pairwise-discriminator 规则"
    assert "弱代理" in seg, "核心纪律缺'不拿弱代理顶替'措辞"


def test_phase4_body_has_scale_signal_counterexample() -> None:
    """Phase 4 段正文(不只是硬checkpoint/自检清单)必须写明规模信号反例, 这是 host 实际执行时读的指令。"""
    text = _SKILL.read_text(encoding="utf-8")
    i_p4 = text.find("### Phase 4")
    i_p5 = text.find("### Phase 5")
    assert i_p4 != -1 and i_p5 != -1 and i_p4 < i_p5
    seg = text[i_p4:i_p5]
    assert "pairwise" in seg or "假设对" in seg, "Phase 4 正文缺 pairwise-discriminator 指令"
    assert "规模信号" in seg, "Phase 4 正文缺规模信号反例"
    assert "共享" in seg, "Phase 4 正文缺'共享基建'反例主体"


def test_checklist_phase1a_and_phase4_updated() -> None:
    """自检清单 Phase 1A 行反映 turn boundary 措辞, Phase 4 行反映 pairwise abstain。"""
    text = _SKILL.read_text(encoding="utf-8")
    i_sc = text.find("## 自检清单")
    i_ref = text.find("## references")
    assert i_sc != -1 and i_ref != -1 and i_sc < i_ref
    sc = text[i_sc:i_ref]
    i_1a_line = sc.find("Phase 1A:")
    i_1b_line = sc.find("Phase 1B:")
    assert i_1a_line != -1 and i_1b_line != -1
    line_1a = sc[i_1a_line:i_1b_line]
    assert "单点硬暂停" not in line_1a, "自检清单 Phase 1A 行仍用旧'单点硬暂停'措辞"
    assert "turn boundary" in line_1a, "自检清单 Phase 1A 行未提 turn boundary"
    i_p4_line = sc.find("Phase 4:")
    i_p5_line = sc.find("Phase 5:")
    assert i_p4_line != -1 and i_p5_line != -1
    line_p4 = sc[i_p4_line:i_p5_line]
    assert "pairwise" in line_p4 or "假设对" in line_p4, "自检清单 Phase 4 行未提 pairwise abstain"


# --- Spec A 附录 C/F: checkpoint 11 字段块 + 降级输出契约 ---

_CHECKPOINT_FIELDS_V2 = [
    "checkpoint_id", "report_path", "phase", "status", "stage_assumption",
    "business_wording", "blast_radius", "excluded_stages", "question",
    "resume_next", "incident_signature",
]

_BLAST_RADIUS_SUBKEYS = ["scope", "brother_comparison", "recent_change"]


def test_output_template_checkpoint_has_11_fields() -> None:
    """checkpoint 块字段集从 10 扩到 11(新增 incident_signature); blast_radius 整体算 1 字段,
    但其 3 个子键(scope/brother_comparison/recent_change)也须在块内可解析出现。

    边界定位: 本文件全篇只有一对三反引号围栏(其余示例均用单反引号内联码), 故正确做法是
    从 i_block 往前找最近的开栏 ``` 、往后找最近的闭栏 ``` , 不能用'再往后找下一个```'
    (那会因全文只有一对围栏而 find 返回 -1, text[i_block:-1] 静默吞掉几乎整个文件, 让断言
    失去位置意义)。"""
    text = _OUT_TMPL.read_text(encoding="utf-8")
    i_block = text.find("OPS_LOCALIZATION_CHECKPOINT")
    assert i_block != -1, "ops 输出模板缺 checkpoint 块"
    i_open = text.rfind("```", 0, i_block)
    i_close = text.find("```", i_block)
    assert i_open != -1 and i_close != -1 and i_open < i_block < i_close, \
        "checkpoint 块围栏定位失败(全文围栏结构变了? 需重新核对本测试的边界逻辑)"
    block = text[i_open:i_close]
    missing = [f for f in _CHECKPOINT_FIELDS_V2 if f not in block]
    assert not missing, f"checkpoint 块缺字段(应 11 个齐): {missing}"
    missing_sub = [k for k in _BLAST_RADIUS_SUBKEYS if k not in block]
    assert not missing_sub, f"checkpoint 块 blast_radius 缺结构化子键: {missing_sub}"


def test_output_template_incident_signature_algorithm_deterministic() -> None:
    """incident_signature 必须是文档化的确定性算法(sha256 + norm 四字段拼接), 不是'抽关键词'。"""
    text = _OUT_TMPL.read_text(encoding="utf-8")
    assert "sha256" in text, "ops 输出模板缺 incident_signature 的 sha256 算法说明"
    assert "norm(failed_action)" in text, "ops 输出模板缺 incident_signature 算法的 failed_action 项"
    assert "norm(business_wording)" in text, "ops 输出模板缺 incident_signature 算法的 business_wording 项"
    assert "不让模型抽关键词" in text, "ops 输出模板缺'不让模型抽关键词'的确定性声明"


def test_output_template_degraded_contract_present() -> None:
    """assumed_unconfirmed 降级输出契约: banner 置顶 + Phase4 降级模式指针。"""
    text = _OUT_TMPL.read_text(encoding="utf-8")
    i_0a = text.find("## 0A.")
    i_1 = text.find("## 1.", i_0a)
    assert i_0a != -1 and i_1 != -1 and i_0a < i_1
    seg = text[i_0a:i_1]
    for token in ["降级输出契约", "阶段理解未确认", "置顶", "不给单一头名"]:
        assert token in seg, f"0A 段缺降级输出契约 token: {token}"


def test_output_template_four_terminal_states() -> None:
    """checkpoint 终态从旧 3 个(stage_status)扩到 4 个(status 字段名统一, 含 cancelled)。"""
    text = _OUT_TMPL.read_text(encoding="utf-8")
    i_0a = text.find("## 0A.")
    i_1 = text.find("## 1.", i_0a)
    seg = text[i_0a:i_1]
    assert "stage_status" not in seg, "0A 段仍用旧字段名 stage_status(应统一为 status)"
    for token in ["confirmed", "assumed_with_user_correction", "assumed_unconfirmed", "cancelled"]:
        assert token in seg, f"0A 段缺终态 token: {token}"


# --- Spec A 附录 D: 排序段 pairwise-discriminator(ops输出模板.md 侧) ---


def test_output_template_ranking_section_has_pairwise_rule() -> None:
    """§2 排序规则必须含 pairwise-discriminator 裁决 + 规模信号反例(与 SKILL.md Phase4 正文对应)。"""
    text = _OUT_TMPL.read_text(encoding="utf-8")
    i_s2 = text.find("## 2. 排序规则")
    i_s3 = text.find("## 3. abstain")
    assert i_s2 != -1 and i_s3 != -1 and i_s2 < i_s3
    seg = text[i_s2:i_s3]
    assert "pairwise" in seg or "假设对" in seg, "§2 排序规则缺 pairwise-discriminator 裁决"
    assert "规模信号" in seg, "§2 排序规则缺规模信号反例"
    assert "共享基建" in seg, "§2 排序规则缺'共享基建'反例主体"
    assert "兄弟" in seg, "§2 排序规则缺兄弟对照作为决定性信号的说明"


def test_output_template_ranking_output_format_covers_abstain_pair() -> None:
    """排序输出格式须支持'某对因信号缺失 abstain'的写法(不强行武断排序)。"""
    text = _OUT_TMPL.read_text(encoding="utf-8")
    i_s2 = text.find("## 2. 排序规则")
    i_s3 = text.find("## 3. abstain")
    seg = text[i_s2:i_s3]
    assert "待确认" in seg, "§2 排序输出格式缺'待确认'式弃权写法"


# --- Spec A 附录 G: §2-enum 分歧走乙(目录示例化 + 枚举受控种子) ---

def test_behavior_ref_section2_has_illustrative_declaration() -> None:
    """§2 mechanism_tag 说明性命名声明: 本节 tag 非受控注册, 受控见 MECHANISM_TAGS。"""
    text = _BEHAVIOR_REF.read_text(encoding="utf-8")
    i_s2 = text.find("## 2. 每类 textbook signature")
    i_s3 = text.find("## 3. 生命周期时点矩阵")
    assert i_s2 != -1 and i_s3 != -1 and i_s2 < i_s3
    seg = text[i_s2:i_s3]
    assert "说明性命名示例" in seg, "§2 缺 mechanism_tag 说明性命名声明"
    assert "非受控注册 tag" in seg, "§2 缺'非受控注册 tag'措辞"
    assert "MECHANISM_TAGS" in seg, "§2 声明未指向 MECHANISM_TAGS 受控枚举"


def test_behavior_ref_section4_clarifies_examples_illustrative() -> None:
    """§4 命名规范里举的例子(如 config_default_fallback)本身可能不在枚举里, 须加澄清避免误导。"""
    text = _BEHAVIOR_REF.read_text(encoding="utf-8")
    i_s4 = text.find("## 4. mechanism_tag 命名规范")
    i_s5 = text.find("## 5. ")
    assert i_s4 != -1 and i_s5 != -1 and i_s4 < i_s5
    seg = text[i_s4:i_s5]
    assert "以 `MECHANISM_TAGS`" in seg or "以 MECHANISM_TAGS" in seg, \
        "§4 举例句缺'以 MECHANISM_TAGS(code)为准'澄清"


# --- Spec A 附录 H: 文档同步(F4 正面回应) ---

_GUIDE = _REPO / "docs" / "使用指南" / "ops-localization-使用指南.md"
_BLIND_PLAN = _REPO / "docs" / "测试" / "ops-localization-真盲测计划.md"


def test_use_guide_mentions_phase1a_pause_and_resume() -> None:
    """使用指南必须向读者交代: 会暂停问你 + 怎么答 + 阶段未确认 banner 含义 + resume。"""
    text = _GUIDE.read_text(encoding="utf-8")
    for token in ["Phase 1A", "暂停", "resume", "assumed_unconfirmed"]:
        assert token in text, f"使用指南缺 token: {token}"


def test_blind_plan_has_phase1a_and_pairwise_acceptance_points() -> None:
    """真盲测计划验收点必须补 Phase 1A / 暂停或降级 / resume抗污染 / pairwise abstain。"""
    text = _BLIND_PLAN.read_text(encoding="utf-8")
    for token in ["Phase 1A", "resume", "pairwise", "turn boundary"]:
        assert token in text, f"真盲测计划缺验收点 token: {token}"


def test_blind_plan_has_neutral_regression_pattern_not_hardcoded_case() -> None:
    """回归案例须写成领域无关 pattern(不硬编具体工单/短码), 真案例指向 gitignored blind-cases.md。
    用短码**形状**的正则判断(`*NNN#` 这类 USSD 短码模式), 不硬编具体号码——否则测试源码本身
    (tracked 文件)就会把真实短码字面值焊进去, 犯 Task 8 那条同样的错误。"""
    text = _BLIND_PLAN.read_text(encoding="utf-8")
    assert "blind-cases.md" in text, "真盲测计划未指向 gitignored 案例集"
    assert not re.search(r"\*\d{2,4}#", text), \
        "真盲测计划残留 USSD 风格短码字面值(应中性化, 真案例移 gitignored)"


def test_docs_no_sample_codename_denylist_gate() -> None:
    """扩展既有 `test_no_sample_codename_in_ops_skill` 同一套 gitignored denylist 机制,
    覆盖使用指南 + 真盲测计划(之前只扫 `.claude/skills/ops-localization/`, 没扫这两份 doc)。
    收 review Finding:短码正则只堵得住 `*NNN#` 带符号形态, 堵不住裸数字/裸类名/裸表名这类
    denylist 里任意字符串形态——denylist 机制天然覆盖任意形态, 比针对某一类形态手写窄正则
    更不容易漏(Task 5 commit body 就出过一次裸数字形态短码漏过短码正则的真实例子, 已在本轮修正)。
    清单缺失时 skip(CI-safe, 与既有测试同口径)。"""
    if not _DENYLIST.exists():
        pytest.skip(f"代号清单 gitignored 缺失({_DENYLIST}):本地跑才扫,CI 由人工兜底")
    raw = _DENYLIST.read_text(encoding="utf-8").splitlines()
    terms = [t.strip() for t in raw if t.strip() and not t.strip().startswith("#") and len(t.strip()) >= 3]
    assert terms, f"代号清单为空: {_DENYLIST}(fail-closed)"
    offenders: dict[str, list[str]] = {}
    for doc in (_GUIDE, _BLIND_PLAN):
        low = doc.read_text(encoding="utf-8").lower()
        hits = [t for t in terms if t.lower() in low]
        if hits:
            offenders[str(doc.relative_to(_REPO))] = hits
    assert not offenders, f"ops tracked 文档命中样本代号(应只落 gitignored): {offenders}"


# --- Spec A 收尾: SKILL.md 与 ops输出模板.md 的 4 终态集合必须一致(跨文件契约) ---

_TERMINAL_STATES = {"confirmed", "assumed_with_user_correction", "assumed_unconfirmed", "cancelled"}


def test_terminal_states_consistent_across_skill_and_template() -> None:
    """SKILL.md(起手第一步段)与 ops输出模板.md(0A段)的四终态集合必须完全一致,
    防止未来任一文件单独改动导致状态机分叉(F4/一致性教训的正面回应)。"""
    skill_text = _SKILL.read_text(encoding="utf-8")
    i_start = skill_text.find("### 起手第一步")
    i_end = skill_text.find("### Phase 0", i_start)
    skill_seg = skill_text[i_start:i_end]
    skill_states = {s for s in _TERMINAL_STATES if s in skill_seg}

    tmpl_text = _OUT_TMPL.read_text(encoding="utf-8")
    i_0a = tmpl_text.find("## 0A.")
    i_1 = tmpl_text.find("## 1.", i_0a)
    tmpl_seg = tmpl_text[i_0a:i_1]
    tmpl_states = {s for s in _TERMINAL_STATES if s in tmpl_seg}

    assert skill_states == _TERMINAL_STATES, f"SKILL.md 起手第一步段终态集合不全: {skill_states}"
    assert tmpl_states == _TERMINAL_STATES, f"ops输出模板.md 0A段终态集合不全: {tmpl_states}"
