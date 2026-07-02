"""case_store markdown 物化/解析 + 合并测试(spec Appendix A 字段 + 职责 3/4)。

设计思路: render_markdown 只写聚合字段(无 actor_id/source_ref_hash, 那些落 sidecar);
parse_markdown 往返一致;find_by_dedupe_key 扫目录命中;merge_confirmation 只更新计数不覆盖正文。
评分标准: 往返字段不丢;markdown 不含 actor_id/source_ref_hash;合并后 count+1/source_type_counts/
confirmed_by_roles 并入/正文不变;conflict_with 字段渲染。
自动脚本逻辑: 写文件到 tmp_path, parse 回读断言;mutation 思路验"markdown 无审计字段"。
"""
from __future__ import annotations

from pathlib import Path

from contextos.ops.case_store import (
    CaseRecord,
    find_by_dedupe_key,
    merge_confirmation,
    parse_markdown,
    render_markdown,
    write_case,
)


def _rec(**over) -> CaseRecord:
    base = dict(
        case_id="a" * 64,
        dedupe_key="sig\x1fdeferred_charge",
        phenomenon_signature="信用额度内订购大额套餐成功",
        search_terms="递延收费 余额不足 零余额",
        behavior_class="扣费",
        confirmed_root_cause="递延收费 时点解耦",
        mechanism_tag="deferred_charge",
        evidence_pointers=["fqn:com.example.Foo.bar", "table:APP.MML_X"],
        decisive_data_note="charge model 出 index",
        conflict_with=None,
        source_type_counts={"manual": 1},
        confirmed_by_roles=["expert"],
        confirmation_count=1,
        last_confirmed_date="2026-06-29",
    )
    base.update(over)
    return CaseRecord(**base)


def test_render_parse_roundtrip(tmp_path: Path):
    rec = _rec()
    path = write_case(rec, tmp_path)
    assert path.exists()
    back = parse_markdown(path.read_text(encoding="utf-8"))
    assert back.case_id == rec.case_id
    assert back.evidence_pointers == rec.evidence_pointers
    assert back.confirmation_count == 1
    assert back.search_terms == rec.search_terms


def test_markdown_has_no_audit_fields(tmp_path: Path):
    """spec Appendix B MUST: actor_id / source_ref_hash 绝不进 markdown。"""
    rec = _rec()
    text = render_markdown(rec)
    low = text.lower()
    assert "actor" not in low
    assert "source_ref_hash" not in low
    assert "source_ref" not in low


def test_find_by_dedupe_key(tmp_path: Path):
    rec = _rec()
    write_case(rec, tmp_path)
    found = find_by_dedupe_key(tmp_path, "sig\x1fdeferred_charge")
    assert found is not None and found.case_id == rec.case_id
    assert find_by_dedupe_key(tmp_path, "nomatch\x1fx") is None


def test_merge_confirmation_updates_counts_not_body():
    rec = _rec()
    merged = merge_confirmation(rec, source_type="incident", role="ops",
                                date="2026-06-30")
    assert merged.confirmation_count == 2
    assert merged.source_type_counts == {"manual": 1, "incident": 1}
    assert set(merged.confirmed_by_roles) == {"expert", "ops"}
    assert merged.last_confirmed_date == "2026-06-30"
    # 正文不覆盖
    assert merged.confirmed_root_cause == rec.confirmed_root_cause
    assert merged.phenomenon_signature == rec.phenomenon_signature


def test_conflict_with_rendered(tmp_path: Path):
    rec = _rec(conflict_with="b" * 64)
    back = parse_markdown(render_markdown(rec))
    assert back.conflict_with == "b" * 64
