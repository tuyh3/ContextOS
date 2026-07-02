"""confirmed-cases markdown 物化 / 解析 / 查找 / 合并(spec Appendix A)。

case markdown = front-matter(JSON, 结构化字段)+ body(人读正文)。只写聚合字段;
审计字段(actor_id / source_ref_hash)绝不进(spec Appendix B, 落 audit_sidecar)。
find_by_dedupe_key 扫目录 parse front-matter 比对 dedupe_key(去重找候选)。
merge_confirmation 只更新 confirmation_count / source_type_counts / confirmed_by_roles /
last_confirmed_date, 不覆盖根因正文(一致合并分支)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

_FM_START = "<!--ops-case\n"
_FM_END = "\n-->\n"


@dataclass
class CaseRecord:
    case_id: str
    dedupe_key: str
    phenomenon_signature: str
    search_terms: str
    behavior_class: str
    confirmed_root_cause: str
    mechanism_tag: str
    evidence_pointers: list[str]
    decisive_data_note: str | None
    conflict_with: str | None
    source_type_counts: dict[str, int]
    confirmed_by_roles: list[str]
    confirmation_count: int
    last_confirmed_date: str


# front-matter 携带的结构化字段(只聚合, 无审计字段)。
_FM_FIELDS = (
    "case_id", "dedupe_key", "phenomenon_signature", "search_terms",
    "behavior_class", "confirmed_root_cause", "mechanism_tag",
    "evidence_pointers", "decisive_data_note", "conflict_with",
    "source_type_counts", "confirmed_by_roles", "confirmation_count",
    "last_confirmed_date",
)


def render_markdown(rec: CaseRecord) -> str:
    """front-matter(JSON, 机读)+ body(人读 + grep 命中面)。无 actor_id/source_ref_hash。"""
    fm = {k: getattr(rec, k) for k in _FM_FIELDS}
    parts = [_FM_START, json.dumps(fm, ensure_ascii=False, indent=2), _FM_END]
    body = [
        f"# 确诊案例 {rec.case_id[:12]}",
        "",
        f"行为类别: {rec.behavior_class}",
        f"机制族: {rec.mechanism_tag}",
        "",
        "## 现象签名",
        rec.phenomenon_signature,
        "",
        "## 召回锚词",
        rec.search_terms,
        "",
        "## 确认根因",
        rec.confirmed_root_cause,
        "",
        "## 证据指针",
        *[f"- {p}" for p in rec.evidence_pointers],
        "",
        "## 决定性数据",
        rec.decisive_data_note or "(无)",
        "",
        "## 确认元信息(聚合)",
        f"- confirmation_count: {rec.confirmation_count}",
        f"- source_type_counts: {json.dumps(rec.source_type_counts, ensure_ascii=False)}",
        f"- confirmed_by_roles: {', '.join(rec.confirmed_by_roles)}",
        f"- last_confirmed_date: {rec.last_confirmed_date}",
    ]
    if rec.conflict_with:
        body += ["", "## 互斥根因", f"- conflict_with: {rec.conflict_with}"]
    return "".join(parts) + "\n".join(body) + "\n"


def parse_markdown(text: str) -> CaseRecord:
    start = text.index(_FM_START) + len(_FM_START)
    end = text.index(_FM_END, start)
    fm = json.loads(text[start:end])
    return CaseRecord(**{k: fm[k] for k in _FM_FIELDS})


def write_case(rec: CaseRecord, cases_dir: Path) -> Path:
    cases_dir.mkdir(parents=True, exist_ok=True)
    path = cases_dir / f"{rec.case_id}.md"
    path.write_text(render_markdown(rec), encoding="utf-8")
    return path


def find_by_dedupe_key(cases_dir: Path, dedupe_key: str) -> CaseRecord | None:
    """扫目录, 返回**第一个** dedupe_key 命中的 case(去重比对找候选)。

    去重四分支(spec Appendix A 职责 3)在 recorder 层判定;此处只负责按 dedupe_key 取候选。
    多 case 同 dedupe_key(differential / conflict 共存)时返回任一即可——recorder 再按
    root_cause 用 case_id 精确比对决定合并 / 新建。
    """
    if not cases_dir.exists():
        return None
    for p in sorted(cases_dir.glob("*.md")):
        try:
            rec = parse_markdown(p.read_text(encoding="utf-8"))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        if rec.dedupe_key == dedupe_key:
            return rec
    return None


def find_by_case_id(cases_dir: Path, case_id: str) -> CaseRecord | None:
    """精确 case_id 命中(一致合并分支用: 同 dedupe_key + 同 root_cause -> 同 case_id)。"""
    p = cases_dir / f"{case_id}.md"
    if not p.exists():
        return None
    try:
        return parse_markdown(p.read_text(encoding="utf-8"))
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def merge_confirmation(rec: CaseRecord, *, source_type: str, role: str,
                       date: str) -> CaseRecord:
    """一致合并: count+1 / source_type_counts[source_type]+1 / roles 并入 / date 更新。

    **不覆盖根因正文**(spec Appendix A 职责 3 第二支)。返回新 CaseRecord(不就地改)。
    """
    counts = dict(rec.source_type_counts)
    counts[source_type] = counts.get(source_type, 0) + 1
    roles = list(rec.confirmed_by_roles)
    if role not in roles:
        roles.append(role)
    return replace(rec, source_type_counts=counts, confirmed_by_roles=roles,
                   confirmation_count=rec.confirmation_count + 1,
                   last_confirmed_date=date)
