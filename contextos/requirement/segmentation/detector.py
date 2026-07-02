"""Path X: 确定性"编号 -> 层级"切分(后继预期 = Word AutoFormat 反向, 抗脏分隔符)。
raw_text -> list[Segment]。算法见 spec §6.1; spike 实测见证据附录 §1。
"""
from __future__ import annotations

import hashlib
import re

from contextos.requirement.segments import Segment

_ROMAN = {"I": 1, "II": 2, "III": 3, "IIII": 4, "IV": 4, "V": 5,
          "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
_WIDE_SEP = set(".。,，、)）:：．]】")
_TOK = re.compile(r"^(\s*)([0-9]{1,2}|I{2,4}|IV|VI{0,3}|IX|[A-Za-z])(.?)")


def _interps(tok: str) -> list[tuple[str, int]]:
    """token -> [(style, numeric)] 候选。单 I/V/X 给罗马+字母两解, 由落位择优。"""
    if tok.isdigit():
        return [("arabic", int(tok))]
    out: list[tuple[str, int]] = []
    if tok in _ROMAN:
        out.append(("roman", _ROMAN[tok]))
    if len(tok) == 1 and tok.isalpha():
        out.append(("upper" if tok.isupper() else "lower", ord(tok.lower()) - 96))
    return out


def _sid(raw_text: str, suffix: str) -> str:
    h = hashlib.sha1(raw_text.encode("utf-8")).hexdigest()[:6]
    return f"seg-{h}-{suffix}"


def detect(raw_text: str) -> list[Segment]:
    raw_lines = raw_text.splitlines(keepends=True)   # 带行尾, 算真实字符偏移(CRLF 安全)
    lines = [ln.rstrip("\r\n") for ln in raw_lines]
    line_start: list[int] = []
    pos = 0
    for ln in raw_lines:
        line_start.append(pos)
        pos += len(ln)                               # 真实长度(含 \r\n), 不固定 +1(MEDIUM: CRLF)

    # 第一遍: 状态机定每个标记行的落位
    nodes: list[dict] = []
    stack: list[list] = [["root", 0, 0, -1]]   # [style, numeric, level, node_idx]; -1=虚拟根
    sib_count: dict[int, int] = {}

    for li, raw in enumerate(lines):
        if not raw.strip():
            continue
        m = _TOK.match(raw)
        if not m:
            continue
        after = m.group(3)
        interps = _interps(m.group(2))
        if not interps:
            continue
        if after and after not in _WIDE_SEP and not after.isspace():
            continue   # token 后跟字母/数字(如 "Batch" 的 B+a)-> 是单词不是标记(MEDIUM: 边界判断)

        place = None  # (level, parent_node_idx, pop_i_or_None, style, numeric)
        for s, n in interps:                       # 1) 精确后继
            for i in range(len(stack) - 1, 0, -1):
                if stack[i][0] == s and n == stack[i][1] + 1:
                    place = (stack[i][2], stack[i - 1][3], i, s, n)  # 兄弟: 父=上一级(非上一个兄弟自身)
                    break
            if place:
                break
        if place is None:                          # 2) 首项 descend
            for s, n in interps:
                if n == 1:
                    place = (stack[-1][2] + 1, stack[-1][3], None, s, n)
                    break
        sep_ok = after in _WIDE_SEP
        if place is None and sep_ok:               # 3) 跳号后继(仅有分隔符)
            for s, n in interps:
                for i in range(len(stack) - 1, 0, -1):
                    if stack[i][0] == s and stack[i][1] + 1 < n <= stack[i][1] + 4:
                        place = (stack[i][2], stack[i - 1][3], i, s, n)  # 跳号兄弟: 父=上一级
                        break
                if place:
                    break

        conf, reason = "high", ""
        if place is None:
            if not sep_ok:
                continue                           # 无符且非后继/首项 -> 内容行(数字打头内容在此被拒)
            s, n = interps[0]                      # 4) 有符兜底
            placed = None
            for i in range(len(stack) - 1, 0, -1):
                if stack[i][0] == s:
                    placed = (stack[i][2], stack[i - 1][3], i, s, n)  # 兄弟: 父=上一级
                    break
            place = placed or (stack[-1][2] + 1, stack[-1][3], None, s, n)
            conf, reason = "low", "fallback-branch"
        else:
            is_exact_succ = place[2] is not None and place[4] == stack[place[2]][1] + 1
            if not sep_ok and not is_exact_succ:   # 无符只认精确后继(拒 "10 Jan 2026")
                continue

        level, parent_node, pop_i, style, numeric = place
        if pop_i is not None:
            stack = stack[: pop_i + 1]
            stack[pop_i] = [style, numeric, level, len(nodes)]
        else:
            stack.append([style, numeric, level, len(nodes)])
        ordn = sib_count.get(parent_node, 0)
        sib_count[parent_node] = ordn + 1
        nodes.append(dict(line_idx=li, level=level, parent=parent_node,
                          label=m.group(2), style=style, conf=conf, reason=reason, ordinal=ordn))

    # 第二遍: 算 content 跨度 + title_path + id/parent_id
    marker_lines = sorted(n["line_idx"] for n in nodes)

    def next_marker_after(li: int) -> int:
        for ml in marker_lines:
            if ml > li:
                return ml
        return len(lines)

    first_marker = marker_lines[0] if marker_lines else len(lines)
    root_content = "\n".join(x for x in lines[:first_marker] if x.strip())  # 前导正文不丢
    root = Segment(segment_id=_sid(raw_text, "root"), parent_id=None, ordinal=0, level=0,
                   label="", marker_style="none", title_path=[], heading="",
                   content=root_content, start=0, end=len(raw_text), producer="x",
                   confidence="high", confidence_reason="")
    segs = [root]
    idx_to_seg: dict[int, Segment] = {-1: root}

    for ni, n in enumerate(nodes):
        li = n["line_idx"]
        end_line = next_marker_after(li)
        body = "\n".join(x for x in lines[li + 1:end_line] if x.strip())
        end = line_start[end_line] if end_line < len(lines) else len(raw_text)
        parent_seg = idx_to_seg[n["parent"]]
        title_path = [p for p in (parent_seg.title_path + [parent_seg.heading]) if p]
        seg = Segment(
            segment_id=_sid(raw_text, str(ni)), parent_id=parent_seg.segment_id,
            ordinal=n["ordinal"], level=n["level"], label=n["label"],
            marker_style=n["style"], title_path=title_path,
            heading=lines[li].strip(), content=body,
            start=line_start[li], end=end, producer="x",
            confidence=n["conf"], confidence_reason=n["reason"])
        idx_to_seg[ni] = seg
        segs.append(seg)
    return segs
