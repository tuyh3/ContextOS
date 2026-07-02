"""tracked 中性 gate: skill 文件不得含真客户包名(结构性 regex, 不依赖 gitignored denylist)。

设计思路: 真客户标识里最稳的结构特征是 `com.<corp>.` Java 包名(如 com.<realcorp>.)。
合成占位约定只允许 com.example.* / com.x.*(工具签名占位) / com.<x>.*(字面尖括号占位)。
评分标准: 任何 `com.<lowercaseword>.` 且 word 不在 allowlist -> 判泄漏。
脚本逻辑: glob skill md -> 跑 regex -> 收集违例(文件:命中)。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

# repo 根 = 本测试文件向上 3 层(tests -> mcp_server -> contextos -> repo)
_REPO = Path(__file__).resolve().parents[3]
_SKILL_DIR = _REPO / ".claude" / "skills" / "requirement-impact-analysis"

# 允许的合成占位包名第二段
_ALLOWED = {"example", "x"}
# com.<lowercaseword>. ; com.<x>. 字面占位(< 非 [a-z])天然不匹配
_PKG_RE = re.compile(r"com\.([a-z][a-zA-Z0-9_]*)\.")


def _violations(text: str) -> list[str]:
    return [m.group(0) for m in _PKG_RE.finditer(text) if m.group(1) not in _ALLOWED]


def test_regex_catches_a_real_looking_package() -> None:
    # 自检: 探针必须能抓到非 allowlist 的 com.<corp>. 包名
    assert _violations("import com.realcorp.order.Foo;") == ["com.realcorp."]
    # allowlist 占位不报
    assert _violations("com.example.app.X / com.x.y.Z / com.<x>.W") == []


def test_skill_files_have_no_real_package_names() -> None:
    md_files = sorted(_SKILL_DIR.rglob("*.md"))
    assert md_files, f"no skill md under {_SKILL_DIR}"
    offenders = {}
    for f in md_files:
        v = _violations(f.read_text(encoding="utf-8"))
        if v:
            offenders[str(f.relative_to(_REPO))] = sorted(set(v))
    assert not offenders, f"non-neutral package names in skill files: {offenders}"


# ---- ② 概要设计决策层:ADR 模板结构探针 + 样本代号扫(spec 2a1fdc7 D8 / LOW#2)----

# 决策机器速查文件 = ADR 空模板权威出处;输出模板 = 报告结构 + worked example
_ADR_REF = _SKILL_DIR / "references" / "设计决策层-ADR速查.md"
_OUT_TMPL = _SKILL_DIR / "references" / "输出模板.md"
_SKILL = _SKILL_DIR / "SKILL.md"  # 本任务(Task 1)未用;Task 4/5/6 的 D12/D13/D14 探针引用,勿删

# ADR-lite 必写段 token(缺任一 = 模板被抽空,D8)。中性、与具体客户无关。
_ADR_REQUIRED = [
    # 注:ADR 块头"决策 N"不入清单 —— "决策"二字在两文档随处可见,作 token 无独立判别力(no-op)。
    # 结构由下列各必写段 token 把守;块头有这些段即隐含存在。
    "现状证据",        # [事实] 段
    "推断链",          # [推断] 段
    "候选方案",        # >=2 候选(D8 候选 gate)
    "被否方案",        # 被否 + 为什么否
    "概念模型与范围意图",  # D1 概念级
    "兼容边界",        # D8 兼容边界 gate(必写段)
    "证据依赖与降级",  # D6 强制表(降级 gate)
    "翻案条件",        # D8 翻案 gate
    "[推断]",          # D4 第三标签字面量
    "[设计]",          # D4 第三标签字面量
]


def test_adr_speedref_exists_and_has_required_sections() -> None:
    """决策机器速查文件存在且含全部 ADR-lite 必写段(D8:防模板被静默抽空)。"""
    assert _ADR_REF.exists(), f"ADR 速查文件缺失: {_ADR_REF}"
    text = _ADR_REF.read_text(encoding="utf-8")
    missing = [tok for tok in _ADR_REQUIRED if tok not in text]
    assert not missing, f"ADR 速查文件缺必写段 token: {missing}"


def test_output_template_wires_in_adr_section() -> None:
    """DD-2:§3 设计决策段接入报告(塌成 6 段决策块,verbose 模板留 ADR 速查);
    附录 C 保留证据降级机器;worked example 仍中性(master-data 场景 APP_ 表名)。"""
    assert _OUT_TMPL.exists(), f"输出模板缺失: {_OUT_TMPL}"
    text = _OUT_TMPL.read_text(encoding="utf-8")
    assert "设计决策" in text, "输出模板未接入设计决策段"
    # 两层后机器降级表沉附录 C(非正文 verbose 表)
    assert "证据降级" in text, "输出模板附录 C 缺证据降级机器"
    assert "[限制]" in text, "输出模板附录 C 缺截断降级标记 [限制]"
    # worked example 仍中性(master-data 场景用 APP_ 表名,非真客户)
    assert "APP_SERVICE_TYPE" in text, "输出模板缺 master-data worked example(APP_SERVICE_TYPE)"


def test_output_template_requires_inline_design_per_requirement() -> None:
    """可读性 gate(专家骨架):每个需求章就近写本需求设计思路,不只跳全局设计决策章。"""
    assert _OUT_TMPL.exists(), f"输出模板缺失: {_OUT_TMPL}"
    text = _OUT_TMPL.read_text(encoding="utf-8")
    required = [
        "概要设计",                      # 专家骨架章节名
        "设计思路",                      # 每需求章就近设计思路
        "不能只写\"遵循 决策 N\"",        # 命中 ADR 时也要写本需求摘要
    ]
    missing = [tok for tok in required if tok not in text]
    assert not missing, f"输出模板缺逐需求就近设计规则(专家骨架): {missing}"


def test_output_template_requires_concise_current_state_fact_list() -> None:
    """可读性 gate(专家骨架):现状节用现状总表(现状结论|涉及对象|对设计的含义),先结论后证据。"""
    assert _OUT_TMPL.exists(), f"输出模板缺失: {_OUT_TMPL}"
    text = _OUT_TMPL.read_text(encoding="utf-8")
    required = [
        "当前系统现状分析",   # 专家骨架现状章节名(替 X.1)
        "现状总表",
        "现状结论",
        "涉及对象",
        "对设计的含义",
    ]
    missing = [tok for tok in required if tok not in text]
    assert not missing, f"输出模板缺现状总表规则: {missing}"


# 真样本代号清单(gitignored,本地有 / CI 无)。扫 pushed 的 skill 目录防裸代号泄漏。
_DENYLIST = _REPO / "database" / "requirement-skill-denylist.txt"
# 受查面 = 整个 skill 目录(SKILL.md + references;code 分支推它 = 真正 pushed surface)。
# 全 denylist 扫:连 com.<corp>. 正则抓不到的真表名/类名(BS_..._RESTRICTION / ...BCImpl)也拦。
# 注:**不**扫 docs/superpowers 全量 —— 历史 plan/spec 含合法 meta-reference(某 plan 写
# "任何厂商命名空间 com.<vendor>. 即泄漏" 来描述探针本身),全量扫会假阳。② 本批 spec/plan 中性由
# Task 5 scoped shell gate 守(只扫本批两文件)。
_TRACKED_DOC_GLOBS = [_SKILL_DIR]


def _denylist_terms() -> list[str]:
    raw = _DENYLIST.read_text(encoding="utf-8").splitlines()
    # 跳过空行 / # 注释 / 过短(<3,避免误伤)
    return [t.strip() for t in raw if t.strip() and not t.strip().startswith("#") and len(t.strip()) >= 3]


def test_no_sample_codename_in_tracked_docs() -> None:
    """pushed 的 skill 目录(SKILL.md + references,code 分支推它)不得含 gitignored 代号清单里的裸代号(LOW#2)。
    代号清单 gitignored -> 缺失时本单测 skip(CI-safe,不破 fresh clone);本地 fail-closed
    由 Task 5 shell gate 守(见 Task 5 Step 1)。存在时机械扫,命中即 fail。
    注:② 本批 spec/plan 的中性走 Task 5 scoped gate(避历史 doc 合法 meta-reference 假阳)。"""
    if not _DENYLIST.exists():
        pytest.skip(f"代号清单 gitignored 缺失({_DENYLIST}):本地跑才扫,CI 由人工兜底")
    terms = _denylist_terms()
    assert terms, f"代号清单为空: {_DENYLIST}(fail-closed:至少放 1 个样本代号)"
    offenders: dict[str, list[str]] = {}
    for root in _TRACKED_DOC_GLOBS:
        if not root.exists():
            continue
        for md in sorted(root.rglob("*.md")):
            low = md.read_text(encoding="utf-8").lower()
            hits = [t for t in terms if t.lower() in low]
            if hits:
                offenders[str(md.relative_to(_REPO))] = hits
    assert not offenders, f"tracked 文档命中样本代号(应只落 gitignored): {offenders}"


# ---- ② 设计决策闭环 gate(D10-D14):spec 2026-06-23-②设计决策闭环gate ----

def test_attribution_table_conform_enum_fixed() -> None:
    """D10:conform 列固定三值枚举,不许自由写(机器出处 = ADR 速查)。"""
    ref = _ADR_REF.read_text(encoding="utf-8")
    required = ["conform", "翻案 -> §3 决策", "gap/待确认", "不许自由写"]
    missing = [t for t in required if t not in ref]
    assert not missing, f"ADR 速查缺 conform 三值枚举固定口径: {missing}"


def test_fork_not_inline_and_decoupled_from_table() -> None:
    """D11:架构 fork 不得 X.3/X.4 内联判决;开不开 ADR 按 D5、进不进表按 D10(三段解耦)。"""
    ref = _ADR_REF.read_text(encoding="utf-8")
    # 注:不用裸 "设计空间"(§7 已有,非 §9 载重,删 §9 的 D5 分支也测不出);
    # 改用只在 §9 出现的短语守 D5 分支 + 无条件内联禁。
    required = [
        "架构 fork 不得 X.3/X.4 内联判决",
        "是否开 ADR 按 D5 影响阈值",
        "是否进落点归属表按 D10 触发",
        "既不强开 ADR,也不内联判决",  # §9 D5 分支 fallback(§9 独有,守"设计空间"语义)
        "内联禁(无条件)",            # §9 收尾一句,守"内联禁是无条件"的载重区分
    ]
    missing = [t for t in required if t not in ref]
    assert not missing, f"ADR 速查缺 D11 三段解耦口径: {missing}"


def test_compat_invariant_promotion_with_limit() -> None:
    """D12:现状编码/前缀/格式约定升为兼容不变量,但只升有约束力的,普通命名风格不升;按已开 ADR 作用域。
    机器在 ADR 速查 §10;宿主自检钩子在 SKILL.md(Phase 3.5/自检清单)—— 两处都守(spec §5+§8)。"""
    ref = _ADR_REF.read_text(encoding="utf-8")
    for tok in ["升为不变量", "普通命名风格不升", "按已开 ADR"]:
        assert tok in ref, f"ADR 速查缺 D12 限制词: {tok}"
    skill = _SKILL.read_text(encoding="utf-8")
    # SKILL.md 必须有 D12 宿主钩子(升格动作 + 限制词),否则 D12 只在速查、宿主指令层不触发
    assert "兼容不变量" in skill, "SKILL.md 缺 D12 兼容不变量升格钩子"
    assert "普通命名风格不升" in skill, "SKILL.md 缺 D12 限制词(普通命名风格不升)"


def test_gui_web_census_gate() -> None:
    """D13:GUI 需求先 census web 层入口;定位不到用 [gap] web 层入口未定位,不混用 [背景-gap]。
    写窄:只禁精确短语 '[背景-gap] web 层入口未定位',不全局禁 [背景-gap]([背景-gap] 在 Phase 1 RAG 合法)。"""
    text = _SKILL.read_text(encoding="utf-8")
    assert "census web 层入口" in text, "SKILL.md 缺 D13 GUI census web 层入口规则"
    assert "[gap] web 层入口未定位" in text, "SKILL.md 缺 D13 正确 gap 标签"
    assert "[背景-gap] web 层入口未定位" not in text, "D13 误用 [背景-gap](该标签 RAG 未命中专用)"


def test_current_state_source_conflict_check() -> None:
    """D14:同一 claim 跨段多源不兼容值 -> [冲突] + 降级;subject+predicate 规范化口径。
    per-file 守(非合并):SKILL.md 钩子 与 输出模板 §4.1 各自都必须带 D14 词汇,删任一处都红
    ([冲突]/subject/predicate 在 BASE 两文件均不存在,故 per-file 断言真载重;'降级' BASE 已有,
    不单独作 token —— 它由各处 D14 文案自带,不另立易假绿的 OR 断言)。"""
    for name, p in (("SKILL.md", _SKILL), ("输出模板", _OUT_TMPL)):
        text = p.read_text(encoding="utf-8")
        for tok in ["[冲突]", "subject", "predicate"]:
            assert tok in text, f"{name} 缺 D14 现状事实源一致性 token: {tok}"


# ---- ② 可读性 v2:审计层与业务层分离(spec 2026-06-23-②可读性v2-审计业务分层)----

# worked example 业务正文段禁出现的所有 bracket 装置(§5.4:无白名单,gap/冲突改大白话)。
# [背景-gap] 单列([背景] 子串不匹配它);[gap]/[冲突] 也禁。脚注 [^a 不是装置,不在禁列。
_BODY_BANNED = [
    "出处:", "read_symbol(", "(归属 L-",
    "[事实]", "[建议]", "[推断]", "[设计]", "[背景]", "[限制]",
    "[gap]", "[冲突]", "[背景-gap]",
]


_APPENDIX_DIVIDER = "==== 溯源附录 ===="


def _worked_example_split() -> tuple[str, str]:
    """把 worked example 切成 (业务正文, 溯源附录)。
    锚点:'## Worked example' 起为 worked example 区(prose 契约段在它之前,
    合法含 [事实]/出处 用于讲格式,不受层边界约束);'==== 溯源附录 ====' 为层分割线
    (用全分割线锚,不用裸 '溯源附录' —— 报告头 prose 会提"末尾溯源附录"致误切)。"""
    text = _OUT_TMPL.read_text(encoding="utf-8")
    assert "## Worked example" in text, "输出模板缺 worked example 锚点"
    we = text.split("## Worked example", 1)[1]
    body, sep, appendix = we.partition(_APPENDIX_DIVIDER)
    assert sep, f"worked example 缺层分割线 '{_APPENDIX_DIVIDER}'"
    return body, appendix


def test_body_layer_has_no_evidence_devices() -> None:
    """核心层边界 gate(spec §7.1/§5.4):worked example 业务正文段(分割线前)零 bracket 装置
    (含 [gap]/[冲突]/[背景-gap]);开放项/矛盾用大白话。机械守"降级不是删、没漏装置进正文"。"""
    body, _ = _worked_example_split()
    offenders = [tok for tok in _BODY_BANNED if tok in body]
    assert not offenders, f"业务正文漏入证据装置(应沉附录): {offenders}"


def test_appendix_layer_has_machine() -> None:
    """层边界 gate 下半(spec §7.1):溯源附录段必须含审计机器 —— 落点归属表(归属决策 + conform)
    + 脚注定义(^[^a 开头行)。"""
    _, appendix = _worked_example_split()
    assert "归属决策" in appendix, "溯源附录缺落点归属表(归属决策列)"
    assert "conform" in appendix, "溯源附录缺 conform 列"
    has_footnote_def = any(line.lstrip().startswith("[^a") and "]:" in line
                           for line in appendix.splitlines())
    assert has_footnote_def, "溯源附录 A 缺脚注定义([^aN]: ...)"


def test_footnote_closure() -> None:
    """脚注闭合(spec §7.2):业务正文每个 [^aN] 引用都在附录 A 有 [^aN]: 定义(无悬空)。"""
    body, appendix = _worked_example_split()
    refs = set(re.findall(r"\[\^(a\d+)\](?!:)", body))   # 正文里的引用(后面不是冒号)
    assert refs, "业务正文未演示任何 [^aN] 脚注引用"
    defs = set(re.findall(r"\[\^(a\d+)\]:", appendix))   # 附录里的定义
    dangling = refs - defs
    assert not dangling, f"业务正文引用了附录未定义的脚注(悬空): {sorted(dangling)}"


def test_current_state_summary_table() -> None:
    """三硬规则之一(spec §5.1):现状节有现状总表三列(现状结论 / 涉及对象 / 对设计的含义)。"""
    body, _ = _worked_example_split()
    for tok in ["现状结论", "涉及对象", "对设计的含义"]:
        assert tok in body, f"worked example 现状总表缺列 token: {tok}"


def test_impact_three_buckets() -> None:
    """三硬规则之二(spec §5.2):影响落点固定三桶子标题。"""
    body, _ = _worked_example_split()
    for tok in ["改动现有", "新增", "不改但需兼容"]:
        assert tok in body, f"worked example 影响落点缺三桶子标题: {tok}"


def test_decision_block_six_segments() -> None:
    """三硬规则之三(spec §5.3):决策块六段固定标签。查**加粗定界**标签(`**决策**` 等),
    它们在 worked example body 各仅出现 1 次、全在 §3 决策块 —— 删决策块则六者全失(真有牙)。
    不查 plain 子串(决策/影响/兼容 在三桶/设计思路等处也现,牙不全)。段数不超靠人工抽审。"""
    body, _ = _worked_example_split()
    for tok in ["**决策**", "**为什么**", "**方案比较**", "**采纳**", "**影响**", "**兼容"]:
        assert tok in body, f"worked example 决策块缺六段加粗标签: {tok}"


def test_appendix_attribution_table_by_name() -> None:
    """附录 B 重定向(spec §7.4):落点归属表在附录段、按落点名 key(无 L-NN);
    >=2 个不同落点名归属同一决策;conform 三值枚举在附录。"""
    _, appendix = _worked_example_split()
    # 逐行按 | 切 cell 的健壮解析(不用跨 cell 正则 —— [^|] 含 \n,标准 GFM 表
    # 行尾 | 会让正则把行间换行误当落点名 group1=\n,实测 0 真命中)。
    rows = []
    for line in appendix.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if set("".join(cells)) <= set("-: "):   # markdown 分隔行 |---|---|
            continue
        name = cells[0]
        adr = None
        for c in cells:
            m = re.search(r"决策\s*(\d+)", c)
            if m:
                adr = m.group(1)
                break
        if name and adr and "落点" not in name:   # 跳表头(落点 列名)
            rows.append((name, adr))
    assert len(rows) >= 2, f"附录 B 落点归属表行 < 2: {rows}"
    top_adr = Counter(adr for _, adr in rows).most_common(1)[0][0]
    names_top = {name for name, adr in rows if adr == top_adr}
    assert len(names_top) >= 2, f"无 >=2 个不同落点名归同一决策: {rows}"
    # conform 三值枚举固定 token 在附录
    for tok in ["conform", "翻案 -> 决策", "gap/待确认"]:
        assert tok in appendix, f"附录 B 缺 conform 三值枚举 token: {tok}"
