"""审计 sidecar(spec Appendix B MUST): confirmed_by_actor_id + source_ref_hash 落
corpus 目录外的 SQLAlchemy 表(红线 #6, 非裸 SQLite, 信创 PG 兼容)。

为什么不进 markdown: confirmed-cases 是 ripgrep sparse 全文搜的扁平文件, rg 不分正文/meta -
一条 case 因别的字段命中被返回时整文件随结果暴露;且 actor 在多用户部署可能是 email/工号
(撞 PII 铁律)。故 actor_id + source_ref_hash 落此表, 绝不进 RAG markdown。
source_ref 进库前 hash(不存原值)。
"""
from __future__ import annotations

import hashlib

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    insert,
    select,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

ops_case_audit = Table(
    "ops_case_audit", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", String(64), nullable=False, index=True),
    Column("confirmed_by_actor_id", Text, nullable=False),
    Column("source_type", String(32), nullable=False),
    Column("source_ref_hash", String(64), nullable=False, default=""),
    Column("created_at", String(40), nullable=False),
)


def create_all(engine: Engine) -> None:
    metadata.create_all(engine, checkfirst=True)


def hash_source_ref(source_ref: str | None) -> str:
    """source_ref 进库前 hash(spec Appendix B); None/空 -> 空串。"""
    if not source_ref:
        return ""
    return hashlib.sha256(source_ref.encode("utf-8")).hexdigest()


def record_audit(engine: Engine, *, case_id: str, confirmed_by_actor_id: str,
                 source_type: str, source_ref: str | None, created_at: str) -> None:
    """写一条 per-confirmation 审计记录。source_ref 自动 hash, 不存原值。"""
    create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(ops_case_audit).values(
            case_id=case_id,
            confirmed_by_actor_id=confirmed_by_actor_id,
            source_type=source_type,
            source_ref_hash=hash_source_ref(source_ref),
            created_at=created_at,
        ))


def read_audit(engine: Engine, case_id: str) -> list[dict]:
    """按 case_id 回读全部 per-confirmation 记录(审计回放)。"""
    create_all(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            select(ops_case_audit).where(ops_case_audit.c.case_id == case_id)
        ).mappings().all()
    return [dict(r) for r in rows]
