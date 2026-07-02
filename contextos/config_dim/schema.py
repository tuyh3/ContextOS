"""配置维度 12 表 SQLAlchemy Core schema(逻辑契约见 design §3.2 + spec §5)。红线#6 非裸 sqlite。"""
from __future__ import annotations

import hashlib

from sqlalchemy import (
    Column, Integer, String, Text, MetaData, Table, Index, UniqueConstraint,
)


def generate_id(prefix: str, *parts: str) -> str:
    """content-derived 稳定 id(同 HIGH 2 ref_key 用 canonical 串,二者皆稳定可复算)。
    String(32) 列容量: prefix + '_' + 24 hex = <=32(prefix<=7)。"""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{h[:24]}"

metadata = MetaData()

config_sources = Table(
    "config_sources", metadata,
    Column("source_id", String(32), primary_key=True),
    Column("source_type", String(16)),       # file / db_table / api / manual
    Column("file_path", String(512), default=""),
    Column("file_type", String(32), default=""),
    Column("framework", String(64), default=""),
    Column("db_name", String(64), default=""),
    Column("owner", String(64), default=""),
    Column("table_name", String(128), default=""),
    Column("row_count", Integer, default=0),
    Column("key_columns", Text, default=""),    # JSON
    Column("value_columns", Text, default=""),  # JSON
    Column("snapshot_sql", Text, default=""),
    Column("module", String(128), default=""),
    Column("env", String(16), default=""),
    Column("description", Text, default=""),
    Column("snapshot_at", String(40), default=""),
    Column("fingerprint", String(64), default=""),
    Index("idx_cs_type", "source_type"),
    Index("idx_cs_module", "module"),
)

config_entities = Table(
    "config_entities", metadata,
    Column("entity_id", String(32), primary_key=True),
    Column("source_id", String(32)),
    Column("entity_key", String(256)),
    Column("entity_type", String(32)),       # file_key / db_table / db_key_pattern
    Column("description", Text, default=""),
    UniqueConstraint("source_id", "entity_key", name="uq_entity"),
    Index("idx_ce_key", "entity_key"),
)

config_snapshots = Table(
    "config_snapshots", metadata,
    Column("snapshot_id", String(32), primary_key=True),
    Column("source_id", String(32)),
    Column("env", String(16)),               # dev/test/prod/all
    Column("version_ref", String(64), default=""),
    Column("created_at", String(40), default=""),
    Column("is_current", Integer, default=1),
    Column("description", Text, default=""),
)

# partial unique (source_id, env) WHERE is_current=1: SQLite/PG 方言适配, 见模块注。
# sqlite_where/postgresql_where 需 SQL 表达式元素(非裸 str), 引用真实 table 列对象,
# 不能用 detached Column(plan 蓝本 `Column("is_current")==1` 在 2.0 会编译错).
Index(
    "uq_snap_current",
    config_snapshots.c.source_id,
    config_snapshots.c.env,
    unique=True,
    sqlite_where=(config_snapshots.c.is_current == 1),
    postgresql_where=(config_snapshots.c.is_current == 1),
)

config_items = Table(
    "config_items", metadata,
    Column("item_id", String(32), primary_key=True),
    Column("source_id", String(32)),
    Column("entity_id", String(32)),
    Column("snapshot_id", String(32)),
    Column("config_key", String(256), default=""),
    Column("key_path", String(512)),
    Column("value_raw", Text, default=""),       # 敏感经 sensitive.sanitize 后落
    Column("value_type", String(16), default=""),
    Column("default_value", Text, default=""),
    Column("scope", String(32), default=""),
    Column("is_sensitive", Integer, default=0),
    Column("value_fingerprint", String(64), default=""),  # HMAC, 仅敏感
    Column("description", Text, default=""),
    UniqueConstraint("source_id", "key_path", "snapshot_id", name="uq_item"),
    Index("idx_ci_key", "config_key"),
    Index("idx_ci_path", "key_path"),
)

config_bindings = Table(
    "config_bindings", metadata,
    Column("binding_id", String(32), primary_key=True),
    Column("entity_id", String(32)),
    Column("bind_type", String(32)),         # java_class/java_method/api/table/domain/sql_template
    Column("bind_target", String(512)),      # canonical: db.owner.table / class_fqn / method_fqn / template_id
    Column("bind_direction", String(16), default=""),  # read/write
    Column("bind_strategy", String(48), default=""),
    Column("evidence", Text, default=""),
    Column("confidence", String(16), default=""),
    UniqueConstraint("entity_id", "bind_type", "bind_target", name="uq_binding"),
    Index("idx_cb_target", "bind_type", "bind_target"),
)

rule_sets = Table(
    "rule_sets", metadata,
    Column("rule_set_id", String(32), primary_key=True),
    Column("name", String(256)),
    Column("source_id", String(32), default=""),
    Column("category", String(64), default=""),
    Column("description", Text, default=""),
    Column("owner_domain", String(128), default=""),
    Column("status", String(16), default=""),
)

# rule_clauses: Scope B 行级, v1 建表不填(populate 留 v2, 见 spec 决策11)
rule_clauses = Table(
    "rule_clauses", metadata,
    Column("clause_id", String(32), primary_key=True),
    Column("rule_set_id", String(32)),
    Column("clause_name", String(256), default=""),
    Column("condition_expr", Text, default=""),
    Column("action_expr", Text, default=""),
    Column("priority", Integer, default=0),
    Column("effective_from", String(40), default=""),
    Column("effective_to", String(40), default=""),
    Column("status", String(16), default=""),
    Column("confidence", String(16), default=""),
)

rule_bindings = Table(
    "rule_bindings", metadata,
    Column("binding_id", String(32), primary_key=True),
    Column("rule_set_id", String(32)),
    Column("bind_type", String(32)),
    Column("bind_target", String(512)),
    Column("bind_role", String(16), default=""),   # trigger/subject/target
    Column("evidence", Text, default=""),
)

config_changes = Table(
    "config_changes", metadata,
    Column("change_id", String(32), primary_key=True),
    Column("source_id", String(32)),
    Column("item_id", String(32), default=""),
    Column("change_type", String(16)),       # add/modify/delete
    Column("old_value", Text, default=""),   # 经 sensitive.sanitize
    Column("new_value", Text, default=""),   # 经 sensitive.sanitize
    Column("changed_at", String(40), default=""),
    Column("changed_by", String(128), default=""),
    Column("change_ref", String(256), default=""),
)

config_evidence = Table(
    "config_evidence", metadata,
    Column("evidence_id", String(32), primary_key=True),
    Column("ref_type", String(16)),          # binding/rule/change
    Column("ref_id", String(32)),
    Column("evidence_type", String(48)),     # 含 rag_business_doc/rag_dict/rag_ddl_comment
    Column("evidence_ref", String(512), default=""),
    Column("excerpt", Text, default=""),     # 经 sensitive.sanitize_text(自由文本 redact)
)

# --- Plan06 overlay 表 ---

owner_resolution = Table(
    "owner_resolution", metadata,
    # HIGH 1(R3): scoped (edge_id, module, datasource_key) —— owner 解析是 module/datasource 级
    Column("edge_id", String(32), primary_key=True),
    Column("module", String(128), primary_key=True, default=""),
    Column("datasource_key", String(128), primary_key=True, default=""),
    Column("resolved_src_db", String(64), default=""),
    Column("resolved_src_owner", String(64), default=""),
    Column("src_resolution_source", String(32), default=""),  # synonym/direct
    Column("src_confidence", String(16), default=""),
    Column("resolved_dst_db", String(64), default=""),
    Column("resolved_dst_owner", String(64), default=""),
    Column("dst_resolution_source", String(32), default=""),
    Column("dst_confidence", String(16), default=""),
    Column("schema_fingerprint", String(64), default=""),
    Column("resolved_at", String(40), default=""),
    Index("idx_or_edge", "edge_id"),
)

config_confirmation = Table(
    "config_confirmation", metadata,
    # HIGH 2: 稳定身份 (customer_id, ref_type, ref_key); ref_key 用 canonical 串
    Column("customer_id", String(64), primary_key=True, default=""),
    Column("ref_type", String(24), primary_key=True),  # config_table/config_entity/binding/rule_set
    Column("ref_key", String(512), primary_key=True),
    Column("decision", String(8)),                     # confirm/reject
    Column("reviewer", String(128), default=""),
    Column("created_at", String(40), default=""),
    Column("schema_fingerprint", String(64), default=""),
    Column("source_fingerprint", String(64), default=""),
)

ALL_TABLES = [t.name for t in metadata.sorted_tables]
