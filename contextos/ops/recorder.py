"""record_confirmed_case 顶层编排(spec Appendix A 职责 1-6, human-gated)。

流程(职责顺序):
  1. 入参校验(RecordCaseInput pydantic 枚举 + 必填)
  2. PII gate(4 检索字段)
  3. evidence_pointers 白名单前缀(fail-closed)
  3'. 去重四分支(按 dedupe_key 找候选; case_id 含 root_cause 故各 case 不撞):
      - dedupe_key 未命中            -> 全新建 case
      - 命中 + root_cause 一致(同 case_id) -> 合并(计数 +1, 不覆盖正文)
      - 命中 + 不同 + differential   -> 新建, 不标 conflict_with
      - 命中 + 不同 + conflict       -> 新建, 标 conflict_with: <候选 case_id>
      - 命中 + 不同 + relation is None -> reject(human-gated: 并存还是互斥只有人能定,
        绝不默认按并存; spec Appendix A 职责 3)
  4. 物化 markdown(写入展开 search_terms; 路径 = confirmed_cases_dir, 写入==检索同口径)
  5. 审计 sidecar(actor_id + source_ref_hash, corpus 目录外)
  6. 同义池积累(本次 search_terms 以 mechanism_tag 归类并入积累池)

返回: {case_id, materialized_path, deduped_into?, conflict_with?}
actor_id: 服务端从认证上下文注入(单机默认 "local-user"), 非 host 参数。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from contextos.ops import paths, synonyms
from contextos.ops.audit_sidecar import record_audit
from contextos.ops.case_schema import RecordCaseInput
from contextos.ops.case_store import (
    CaseRecord,
    find_by_case_id,
    find_by_dedupe_key,
    merge_confirmation,
    write_case,
)
from contextos.ops.dedupe import compute_case_id, compute_dedupe_key
from contextos.ops.evidence_pointers import validate_pointers
from contextos.ops.pii_gate import assert_no_pii

_DEFAULT_ACTOR = "local-user"


def record_confirmed_case_impl(
    app_ctx: Any,
    *,
    phenomenon_signature: str,
    search_terms: str,
    behavior_class: str,
    confirmed_root_cause: str,
    mechanism_tag: str,
    evidence_pointers: list[str],
    decisive_data_note: str | None = None,
    confirmed_by_role: str,
    source_type: str,
    source_ref: str | None = None,
    relation: str | None = None,
    actor_id: str = _DEFAULT_ACTOR,
) -> dict[str, Any]:
    profile = app_ctx.profile
    engine = app_ctx.engine

    # 1. 入参校验(枚举 + 必填非空)
    inp = RecordCaseInput(
        phenomenon_signature=phenomenon_signature,
        search_terms=search_terms,
        behavior_class=behavior_class,
        confirmed_root_cause=confirmed_root_cause,
        mechanism_tag=mechanism_tag,
        evidence_pointers=evidence_pointers,
        decisive_data_note=decisive_data_note,
        confirmed_by_role=confirmed_by_role,
        source_type=source_type,
        source_ref=source_ref,
        relation=relation,
    )

    # 2. PII gate(4 检索字段)
    assert_no_pii({
        "phenomenon_signature": inp.phenomenon_signature,
        "confirmed_root_cause": inp.confirmed_root_cause,
        "decisive_data_note": inp.decisive_data_note,
        "search_terms": inp.search_terms,
    })

    # 3. evidence 白名单(fail-closed)
    validate_pointers(inp.evidence_pointers)

    cases_dir = paths.confirmed_cases_dir(profile)
    vocab_path = paths.ops_vocab_path(profile)
    now = datetime.now()
    today = now.date().isoformat()

    dedupe_key = compute_dedupe_key(inp.phenomenon_signature, inp.mechanism_tag)
    case_id = compute_case_id(dedupe_key, inp.confirmed_root_cause)

    # 3'. 去重四分支
    candidate = find_by_dedupe_key(cases_dir, dedupe_key)
    deduped_into: str | None = None
    conflict_with: str | None = None

    if candidate is None:
        # 分支 1: dedupe_key 未命中 -> 全新建
        rec = _new_record(inp, case_id, dedupe_key, today, conflict_with=None,
                          vocab_path=vocab_path)
        path = write_case(rec, cases_dir)
    else:
        existing = find_by_case_id(cases_dir, case_id)
        if existing is not None:
            # 分支 2: 命中 + root_cause 一致(同 case_id) -> 合并计数不覆盖
            merged = merge_confirmation(existing, source_type=inp.source_type,
                                        role=inp.confirmed_by_role, date=today)
            path = write_case(merged, cases_dir)
            rec = merged
            deduped_into = case_id
        elif inp.relation == "conflict":
            # 分支 4: 命中 + 不同 + conflict -> 新建标 conflict_with
            conflict_with = candidate.case_id
            rec = _new_record(inp, case_id, dedupe_key, today,
                              conflict_with=conflict_with, vocab_path=vocab_path)
            path = write_case(rec, cases_dir)
        elif inp.relation == "differential":
            # 分支 3: 命中 + 不同 + 显式 differential -> 新建不标 conflict_with
            rec = _new_record(inp, case_id, dedupe_key, today, conflict_with=None,
                              vocab_path=vocab_path)
            path = write_case(rec, cases_dir)
        else:
            # 命中 + root_cause 不同 + relation is None -> human-gated reject。
            # differential(互补并存)vs conflict(互斥)是语义判断, 只有人能定; 算法层
            # 靠相同输入(同 dedupe_key + 不同 root_cause)区分不了, 绝不默认按并存
            # (spec Appendix A 职责 3: relation 由人确认时传, 仅后两支需要)。
            raise ValueError(
                "命中同 dedupe_key 但 confirmed_root_cause 不同, 需人工确认 "
                "relation=differential(互补并存) 或 relation=conflict(互斥矛盾) 后重交"
            )

    # 5. 审计 sidecar(actor_id + source_ref_hash, corpus 目录外)
    record_audit(engine, case_id=case_id, confirmed_by_actor_id=actor_id,
                 source_type=inp.source_type, source_ref=inp.source_ref,
                 created_at=now.isoformat())

    # 6. 同义池积累(本次 search_terms 以 mechanism_tag 归类并入)
    synonyms.accumulate(inp.search_terms.split(), inp.mechanism_tag, vocab_path)

    out: dict[str, Any] = {"case_id": case_id, "materialized_path": str(path)}
    if deduped_into is not None:
        out["deduped_into"] = deduped_into
    if conflict_with is not None:
        out["conflict_with"] = conflict_with
    return out


def _new_record(inp: RecordCaseInput, case_id: str, dedupe_key: str, date: str,
                *, conflict_with: str | None, vocab_path: Path) -> CaseRecord:
    """新建 CaseRecord; search_terms 写入展开(spec H.5 双重展开-写入侧)。"""
    expanded = synonyms.expand_terms(inp.search_terms.split(), inp.mechanism_tag,
                                     vocab_path)
    return CaseRecord(
        case_id=case_id,
        dedupe_key=dedupe_key,
        phenomenon_signature=inp.phenomenon_signature,
        search_terms=" ".join(expanded),
        behavior_class=inp.behavior_class,
        confirmed_root_cause=inp.confirmed_root_cause,
        mechanism_tag=inp.mechanism_tag,
        evidence_pointers=list(inp.evidence_pointers),
        decisive_data_note=inp.decisive_data_note,
        conflict_with=conflict_with,
        source_type_counts={inp.source_type: 1},
        confirmed_by_roles=[inp.confirmed_by_role],
        confirmation_count=1,
        last_confirmed_date=date,
    )
