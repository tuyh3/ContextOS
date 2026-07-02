# contextos/config_dim/confirm.py
from __future__ import annotations

from sqlalchemy import select, delete, insert
from sqlalchemy.engine import Engine

from contextos.config_dim.schema import config_confirmation


def ref_key_for(ref_type: str, **parts) -> str:
    """稳定 canonical ref_key(HIGH 2, 不用 ephemeral id)。"""
    if ref_type == "config_table":
        return f"{parts['owner']}.{parts['table']}"
    if ref_type == "config_entity":
        return f"{parts['source']}:{parts['entity_key']}"
    if ref_type == "binding":
        return f"{parts['entity']}|{parts['bind_type']}|{parts['bind_target']}"
    if ref_type == "rule_set":
        return f"{parts['source']}:{parts['name']}"
    raise ValueError(f"unknown ref_type {ref_type}")


def record_decision(engine: Engine, customer_id, ref_type, ref_key, decision, reviewer="",
                    created_at="", schema_fingerprint="", source_fingerprint="") -> None:
    """confirm/reject 库函数(CLI 包它; v1 不做 UI)。upsert by (customer_id,ref_type,ref_key)。"""
    with engine.begin() as c:
        c.execute(delete(config_confirmation).where(
            (config_confirmation.c.customer_id == customer_id) &
            (config_confirmation.c.ref_type == ref_type) &
            (config_confirmation.c.ref_key == ref_key)))
        c.execute(insert(config_confirmation).values(
            customer_id=customer_id, ref_type=ref_type, ref_key=ref_key, decision=decision,
            reviewer=reviewer, created_at=created_at,
            schema_fingerprint=schema_fingerprint, source_fingerprint=source_fingerprint))


def _load(engine: Engine, customer_id: str) -> dict[tuple[str, str], str]:
    with engine.connect() as c:
        rows = c.execute(select(config_confirmation).where(
            config_confirmation.c.customer_id == customer_id)).fetchall()
    return {(r.ref_type, r.ref_key): r.decision for r in rows}


def apply_confirmations(engine: Engine, customer_id: str, candidates: list[dict]) -> list[dict]:
    """权威覆盖层: confirm -> verdict='confirmed'(置信拉满); reject -> 排除; 无 -> 原样。
    优先级 human_confirmed > dict/RAG/Oracle > heuristic。"""
    decisions = _load(engine, customer_id)
    out = []
    for cand in candidates:
        key = (cand.get("ref_type") or "", cand.get("ref_key") or "")  # coerce(pyright clean)
        d = decisions.get(key)
        if d == "reject":
            continue
        if d == "confirm":
            cand = {**cand, "verdict": "confirmed", "confidence": "confirmed"}
        out.append(cand)
    return out
