"""audit_sidecar 测试(spec Appendix B MUST: actor_id + source_ref_hash 落 corpus 外审计表)。

设计思路: ops_case_audit 表(SQLAlchemy, 红线 #6 非裸 SQLite)记 per-confirmation:
case_id / confirmed_by_actor_id / source_type / source_ref_hash / created_at。
source_ref 进库前 hash(不存原值)。
评分标准: create_all 建表;record_audit 写一行;read_audit 按 case_id 回读;source_ref_hash 是
sha256 非原值;actor_id 默认 local-user。
自动脚本逻辑: 真内存 SQLite engine, 写读断言。
"""
from __future__ import annotations

import hashlib

from sqlalchemy import create_engine

from contextos.ops.audit_sidecar import (
    create_all,
    hash_source_ref,
    read_audit,
    record_audit,
)


def test_record_and_read():
    engine = create_engine("sqlite://")
    create_all(engine)
    record_audit(engine, case_id="c1", confirmed_by_actor_id="local-user",
                 source_type="manual", source_ref="ticket-123",
                 created_at="2026-06-29T10:00:00")
    rows = read_audit(engine, "c1")
    assert len(rows) == 1
    row = rows[0]
    assert row["confirmed_by_actor_id"] == "local-user"
    assert row["source_type"] == "manual"
    # source_ref 不存原值, 存 hash
    assert row["source_ref_hash"] == hashlib.sha256(b"ticket-123").hexdigest()
    assert "source_ref" not in row or row.get("source_ref") != "ticket-123"


def test_hash_source_ref_none():
    assert hash_source_ref(None) == ""
    assert hash_source_ref("") == ""


def test_multiple_confirmations_appended():
    engine = create_engine("sqlite://")
    create_all(engine)
    record_audit(engine, case_id="c2", confirmed_by_actor_id="a",
                 source_type="manual", source_ref=None, created_at="t1")
    record_audit(engine, case_id="c2", confirmed_by_actor_id="b",
                 source_type="incident", source_ref="x", created_at="t2")
    rows = read_audit(engine, "c2")
    assert len(rows) == 2
