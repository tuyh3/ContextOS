"""record_confirmed_case_impl 顶层编排测试(spec Appendix A 职责 1-6 + Appendix E 验收锁)。

设计思路: 编排 = 入参校验 -> PII gate -> evidence 白名单 -> 去重四分支 -> 物化 markdown ->
sidecar 写 -> 同义池积累。返回 {case_id, materialized_path, deduped_into?, conflict_with?}。
评分标准(spec Appendix E):
  [去重四分支] 未命中->全新建; 命中+一致->合并计数不覆盖; 命中+不同+differential->新建不标
              conflict_with; 命中+不同+conflict->新建标 conflict_with;
              命中+不同+relation is None->reject(human-gated, 不默认并存)。
  [case_id 不撞] 同 dedupe_key 不同 root_cause -> case_id 不同。
  [actor/ref sidecar] actor_id + source_ref_hash 不在 markdown, 落 sidecar。
  [PII gate] 含 PII -> reject。
  [同义池] 积累后 vocab 文件含本次 search_terms。
  [路径同口径] 写入路径 == confirmed_cases_dir(profile)(resolver 一致)。
自动脚本逻辑: fake_ops_app_ctx(真内存 SQLite engine), tmp data_dir, 逐分支构造调用。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextos.ops import paths, synonyms
from contextos.ops.audit_sidecar import read_audit
from contextos.ops.case_store import find_by_case_id
from contextos.ops.pii_gate import PiiGateError
from contextos.ops.recorder import record_confirmed_case_impl


def _base_kwargs(**over) -> dict:
    base = dict(
        phenomenon_signature="信用额度内订购大额套餐成功",
        search_terms="递延收费 余额不足",
        behavior_class="扣费",
        confirmed_root_cause="递延收费 时点解耦",
        mechanism_tag="deferred_charge",
        evidence_pointers=["fqn:com.example.Foo.bar"],
        decisive_data_note="charge model 出 index",
        confirmed_by_role="expert",
        source_type="manual",
        source_ref="ticket-1",
        relation=None,
    )
    base.update(over)
    return base


def test_branch_new_case(fake_ops_app_ctx):
    res = record_confirmed_case_impl(fake_ops_app_ctx, **_base_kwargs())
    assert "case_id" in res
    assert res.get("deduped_into") is None
    cases_dir = paths.confirmed_cases_dir(fake_ops_app_ctx.profile)
    assert Path(res["materialized_path"]).parent == cases_dir   # 路径同口径
    assert (cases_dir / f"{res['case_id']}.md").exists()


def test_branch_merge_consistent(fake_ops_app_ctx):
    """命中 + root_cause 一致 -> 合并计数不覆盖正文, 返回 deduped_into。"""
    r1 = record_confirmed_case_impl(fake_ops_app_ctx, **_base_kwargs())
    r2 = record_confirmed_case_impl(
        fake_ops_app_ctx,
        **_base_kwargs(source_type="incident", confirmed_by_role="ops"))
    assert r2["case_id"] == r1["case_id"]
    assert r2.get("deduped_into") == r1["case_id"]
    rec = find_by_case_id(paths.confirmed_cases_dir(fake_ops_app_ctx.profile), r1["case_id"])
    assert rec.confirmation_count == 2
    assert rec.source_type_counts == {"manual": 1, "incident": 1}
    assert set(rec.confirmed_by_roles) == {"expert", "ops"}


def test_branch_differential_no_conflict(fake_ops_app_ctx):
    """命中 + root_cause 不同 + relation=differential -> 新建, 不标 conflict_with。"""
    r1 = record_confirmed_case_impl(fake_ops_app_ctx, **_base_kwargs())
    r2 = record_confirmed_case_impl(
        fake_ops_app_ctx,
        **_base_kwargs(confirmed_root_cause="订购路径无余额闸",
                       relation="differential"))
    assert r2["case_id"] != r1["case_id"]        # case_id 不撞
    assert r2.get("conflict_with") is None
    assert r2.get("deduped_into") is None
    rec = find_by_case_id(paths.confirmed_cases_dir(fake_ops_app_ctx.profile), r2["case_id"])
    assert rec.conflict_with is None


def test_different_root_cause_without_relation_rejected(fake_ops_app_ctx):
    """命中同 dedupe_key + root_cause 不同 + relation is None -> reject(human-gated)。
    并存 vs 互斥只有人能定, 绝不默认按并存(spec Appendix A 职责 3)。"""
    record_confirmed_case_impl(fake_ops_app_ctx, **_base_kwargs())
    with pytest.raises(ValueError):
        record_confirmed_case_impl(
            fake_ops_app_ctx,
            **_base_kwargs(confirmed_root_cause="订购路径无余额闸", relation=None))


def test_branch_conflict_marks_conflict_with(fake_ops_app_ctx):
    """命中 + root_cause 不同 + relation=conflict -> 新建, 标 conflict_with。"""
    r1 = record_confirmed_case_impl(fake_ops_app_ctx, **_base_kwargs())
    r2 = record_confirmed_case_impl(
        fake_ops_app_ctx,
        **_base_kwargs(confirmed_root_cause="订购路径无余额闸",
                       relation="conflict"))
    assert r2["case_id"] != r1["case_id"]
    assert r2.get("conflict_with") == r1["case_id"]
    rec = find_by_case_id(paths.confirmed_cases_dir(fake_ops_app_ctx.profile), r2["case_id"])
    assert rec.conflict_with == r1["case_id"]


def test_audit_sidecar_not_in_markdown(fake_ops_app_ctx):
    """actor_id + source_ref_hash 落 sidecar, 不在 markdown。"""
    res = record_confirmed_case_impl(fake_ops_app_ctx, **_base_kwargs())
    md = (paths.confirmed_cases_dir(fake_ops_app_ctx.profile)
          / f"{res['case_id']}.md").read_text(encoding="utf-8")
    assert "local-user" not in md
    assert "ticket-1" not in md
    rows = read_audit(fake_ops_app_ctx.engine, res["case_id"])
    assert len(rows) == 1
    assert rows[0]["confirmed_by_actor_id"] == "local-user"   # 单机默认 actor
    assert rows[0]["source_ref_hash"] and "ticket-1" not in rows[0]["source_ref_hash"]


def test_pii_rejected(fake_ops_app_ctx):
    with pytest.raises(PiiGateError):
        record_confirmed_case_impl(
            fake_ops_app_ctx,
            **_base_kwargs(phenomenon_signature="用户 13800138000 订购失败"))


def test_bad_evidence_pointer_rejected(fake_ops_app_ctx):
    from contextos.ops.evidence_pointers import EvidencePointerError
    with pytest.raises(EvidencePointerError):
        record_confirmed_case_impl(
            fake_ops_app_ctx, **_base_kwargs(evidence_pointers=["SELECT * FROM X"]))


def test_synonym_pool_accumulated(fake_ops_app_ctx):
    record_confirmed_case_impl(fake_ops_app_ctx, **_base_kwargs(search_terms="新锚词A 新锚词B"))
    vocab = paths.ops_vocab_path(fake_ops_app_ctx.profile)
    data = json.loads(vocab.read_text(encoding="utf-8"))
    assert "新锚词A" in data["deferred_charge"]["variants"]


def test_search_terms_expanded_in_markdown(fake_ops_app_ctx):
    """写入展开: case 的 search_terms 行含同义池展开的 variants(spec H.5 双重展开-写入)。"""
    res = record_confirmed_case_impl(fake_ops_app_ctx, **_base_kwargs(search_terms="递延收费"))
    rec = find_by_case_id(paths.confirmed_cases_dir(fake_ops_app_ctx.profile), res["case_id"])
    # deferred_charge 种子 variants 含 "时点解耦" -> 写入展开后应出现
    assert "时点解耦" in rec.search_terms
