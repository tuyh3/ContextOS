"""Path X 确定性切分测试(中性合成 fixture)。

设计思路: 复刻 spike 验过的难点 —— 后继预期消解罗马/字母 I 歧义、宽分隔符 + 无符精确
后继抗脏输入、数字打头内容行不误检、小写下嵌大写(API 参数)的杀手案。
评分标准: 每个标记的 parent_id 必须对(parent 准度); 数字打头内容行不得产 Segment。
脚本逻辑: detect(raw_text) -> list[Segment]; 用 (label, level, parent.label) 三元组比对。
"""
from __future__ import annotations

from contextos.requirement.segmentation.detector import detect

# 中性合成需求(脏分隔符混用 + 罗马 IIII + 小写下嵌大写 + 数字打头内容行)
FIXTURE = """模块说明：
1. 配置项可逐项开关
2、前台界面
    A, 表单提交动作
    B：批量确认动作
    C 资格查询界面
        I， 是否启用
        II。是否在白名单
        III 满足天数
        IIII 满足用量
    D，历史查询界面
3，限制逻辑
    a, 单项最多两个目标
    b 可多次提交
4，接口清单
    a，修改 QueryDetail 接口，增加如下输出参数
        A，标记位 ，0—否, 1—是
        B，子项汇总
        C，共享汇总
        D，实例编号
    b， 新接口取价
    10 Jan 2026 更新：本段为说明文字非编号项
    c，新接口确认
"""


def _triples(segs):
    by_id = {s.segment_id: s for s in segs}
    out = []
    for s in segs:
        if s.level == 0:
            continue
        parent = by_id.get(s.parent_id)
        out.append((s.label, s.level, parent.label if parent and parent.level > 0 else "根"))
    return out


def test_detect_nesting_and_successor():
    segs = detect(FIXTURE)
    t = _triples(segs)
    # 一级
    assert ("1", 1, "根") in t and ("2", 1, "根") in t and ("3", 1, "根") in t and ("4", 1, "根") in t
    # 2 下的 A~D 二级
    for lab in ("A", "B", "C", "D"):
        assert (lab, 2, "2") in t, f"{lab} 应是 2 的二级子"
    # C 下的罗马 I~IIII 三级(后继预期消解 I 歧义; IIII 畸形罗马)
    for lab in ("I", "II", "III", "IIII"):
        assert (lab, 3, "C") in t, f"{lab} 应是 C 的三级子"


def test_detect_killer_lowercase_then_uppercase():
    """4 > a > A/B/C/D: 大写 A~D 在此是三级(API 输出参数), 不是二级。"""
    segs = detect(FIXTURE)
    t = _triples(segs)
    assert ("a", 2, "4") in t           # 4 的二级小写 a
    for lab in ("A", "B", "C", "D"):
        assert (lab, 3, "a") in t, f"{lab} 应是 4>a 的三级"
    assert ("b", 2, "4") in t and ("c", 2, "4") in t


def test_detect_rejects_number_led_content_line():
    """'10 Jan 2026 更新…' 数字打头但无分隔符且非精确后继 -> 不产 Segment。"""
    segs = detect(FIXTURE)
    headings = [s.heading for s in segs]
    assert not any(h.startswith("10 Jan") for h in headings)


def test_detect_no_spurious_markers():
    """合成 fixture 共 1/2/3/4 + A/B/C/D + I/II/III/IIII + a/b(3下) + a/A/B/C/D/b/c(4下)
    = 4 + 4 + 4 + 2 + 7 = 21 个真标记, 不多不少。"""
    segs = detect(FIXTURE)
    markers = [s for s in segs if s.level > 0]
    assert len(markers) == 21, [s.heading[:12] for s in markers]


def test_detect_rejects_plain_word_as_successor():
    """'A,' 后跟普通英文内容行 'Batch ...' 不能被当成后继 B(无分隔符且 token 后是字母)。"""
    raw = ("1. 模块\n"
           "    A, 表单提交动作\n"
           "    Batch operation should not become marker B\n"
           "    B, 真正的 B 项\n")
    segs = detect(raw)
    headings = [s.heading for s in segs if s.level > 0]
    assert not any(h.startswith("Batch") for h in headings)   # Batch 不成标记
    assert any(h.startswith("A,") for h in headings)
    assert any(h.startswith("B,") for h in headings)          # 真 B(带分隔符)仍在


def test_detect_offsets_correct_with_crlf():
    """\\r\\n 文本的 start/end 是 raw_text 真实字符偏移(不被固定 +1 偏)。"""
    raw = "1. 甲项说明\r\n2. 乙项说明\r\n"
    segs = detect(raw)
    for s in segs:
        if s.level == 0:
            continue
        # 偏移区间去前导空白后应以本段标号开头
        assert raw[s.start:s.end].lstrip().startswith(s.label), (s.label, repr(raw[s.start:s.end]))


def test_detect_rejects_no_separator_first_item():
    """无分隔符的首项(非精确后继)不成标记: 'A 文字' 被 line-98 guard 拒。"""
    raw = ("1. 模块\n"
           "    A 无分隔符首项不应成标记\n"
           "    B, 真正的 B 带分隔符\n")
    headings = [s.heading for s in detect(raw) if s.level > 0]
    assert not any(h.startswith("A ") for h in headings)   # 无符首项被拒
    assert any(h.startswith("B,") for h in headings)        # 带符的真 B 仍在


def test_detect_skip_number_bound_keeps_fallback_low_conf():
    """跳号上界 +4 守门: 阿拉伯数字带分隔符但跳幅 >4(1 -> 8, gap=7)不算跳号后继。

    设计思路: 探针把 detector.py:78 的 '+ 4' 放宽到 '+ 99' 后 6 个老测试仍全绿,说明
    跳号上界这个分支没被任何断言钉住。本测试钉的是"跳太远落兜底"的置信度语义,而非
    "8 不成标记"——因为 8 带分隔符,无论上界宽窄它都会成标记,只是来路不同:
      原 +4 界: 1 -> 8 (gap 7 > 4) 不匹配 stage-3 跳号 -> 落 stage-4 有符兜底
                -> confidence='low', confidence_reason='fallback-branch'
      若 +99:   1 -> 8 命中 stage-3 跳号后继 -> confidence='high', reason=''
    评分标准: 8 段必须是 low + 'fallback-branch'(原 detector 的真实落位)。
    脚本逻辑: detect('1, 甲\\n8, 跳太多\\n') 取 label=='8' 的 Segment 验 confidence 二元组。
    判别性(经验实证): 已临时把 +4 改 +99 真跑,8 段 confidence 翻成 high/'',
    本断言随即 FAIL;改回 +4 即 PASS。故 (a) 原 detector 过 (b) 放宽上界则挂。
    """
    raw = "1, 甲\n8, 跳太多\n"
    segs = detect(raw)
    skip_seg = next(s for s in segs if s.label == "8")
    assert skip_seg.confidence == "low"                       # 跳太远未命中跳号 -> 落兜底
    assert skip_seg.confidence_reason == "fallback-branch"    # 来路是有符兜底, 非跳号后继
