"""code_* 投影表(04 §7.1 契约, 表名 code_* + lang 列 = spec D5)。

红线定性(构建契约 §2 五守则 + spec §3.1 受控派生引擎):
- binding 只由 JDT 引擎做(vendored java-indexer = JDT Core 同源快照, 跑完即弃)
- 单向派生: workspace/源码 -> 表, 永不反向; 表不是真相
- 可丢弃重建: ensure_projection_schema 版本不符直接 DROP + 重建(不走列级迁移)
- 不引图谱产品; 物理走 SQLAlchemy 存储抽象(信创 PG 兼容)
"""
from __future__ import annotations

from sqlalchemy import Column, Index, Integer, MetaData, String, Table, Text, insert, select
from sqlalchemy.engine import Engine

PROJECTION_SCHEMA_VERSION = "3"  # v3: inheritance/table_refs 也上 row_id 代理 PK(F1 同类补全, 跨模块重复 FQN 增量撞复合 PK); v2: 四实体表代理 PK + class_fqn 去 unique

metadata = MetaData()

code_files = Table(
    "code_files", metadata,
    Column("file_path", String(512), primary_key=True),   # 相对仓根
    Column("lang", String(16), nullable=False, default="java"),
    Column("module", String(128), default=""),
    Column("package_name", String(256), default=""),
    Column("sha1", String(40), default=""),
)

# F1: jar 的 class_id/method_id/field_id/call_id 是 run-scoped(每次运行从 C1/M1/F1/X1
# 重新计数), 增量子集会与库内未触碰文件的 id 相撞 -> 不可当 PK。代理主键 row_id
# (照 code_references.ref_id 模式), 原 jar id 降为普通溯源列。join 全走 FQN。
code_classes = Table(
    "code_classes", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("class_id", String(64), default=""),            # jar 来源 id, 仅溯源用
    Column("lang", String(16), nullable=False, default="java"),
    Column("class_fqn", String(512), nullable=False),
    Column("class_name", String(256), nullable=False),
    Column("name_lower", String(256), nullable=False, default=""),
    Column("package_name", String(256), default=""),
    Column("source_file", String(512), nullable=False, default=""),
    Column("kind", String(16), default=""),               # class|interface|enum
    Column("superclass", String(512), default=""),
    Column("interfaces_json", Text, default="[]"),
    Column("modifiers_json", Text, default="[]"),
    Column("annotations_json", Text, default="[]"),
    Column("start_line", Integer, default=0),
    Column("end_line", Integer, default=0),
)
# F3: 真实世界跨模块重复 FQN 存在(legacy 复制粘贴工具类), 唯一性不是事实约束
# (spec §4 修订记 T17) -> 非 unique 普通索引。
Index("idx_cc_fqn", code_classes.c.class_fqn, unique=False)
Index("idx_cc_name", code_classes.c.class_name)
Index("idx_cc_name_lower", code_classes.c.name_lower)
Index("idx_cc_source_file", code_classes.c.source_file)

code_methods = Table(
    "code_methods", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("method_id", String(64), default=""),           # jar 来源 id, 仅溯源用
    Column("lang", String(16), nullable=False, default="java"),
    Column("class_fqn", String(512), nullable=False),
    Column("method_name", String(256), nullable=False),
    Column("name_lower", String(256), nullable=False, default=""),
    Column("signature", String(1024), default=""),
    Column("method_fqn", String(1024), default=""),
    Column("return_type", String(256), default=""),
    Column("param_types_json", Text, default="[]"),
    Column("param_names_json", Text, default="[]"),
    Column("modifiers_json", Text, default="[]"),
    Column("annotations_json", Text, default="[]"),
    Column("is_constructor", Integer, default=0),
    Column("source_file", String(512), nullable=False, default=""),
    Column("start_line", Integer, default=0),
    Column("end_line", Integer, default=0),
)
Index("idx_cm_fqn", code_methods.c.method_fqn)
Index("idx_cm_name", code_methods.c.method_name)
Index("idx_cm_name_lower", code_methods.c.name_lower)
Index("idx_cm_class", code_methods.c.class_fqn)
Index("idx_cm_source_file", code_methods.c.source_file)

code_fields = Table(
    "code_fields", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("field_id", String(64), default=""),            # jar 来源 id, 仅溯源用
    Column("lang", String(16), nullable=False, default="java"),
    Column("class_fqn", String(512), nullable=False),
    Column("field_name", String(256), nullable=False),
    Column("name_lower", String(256), nullable=False, default=""),
    Column("field_type", String(256), default=""),
    Column("modifiers_json", Text, default="[]"),
    Column("annotations_json", Text, default="[]"),
    Column("source_file", String(512), nullable=False, default=""),
    Column("start_line", Integer, default=0),
    Column("end_line", Integer, default=0),
)
Index("idx_cf_name", code_fields.c.field_name)
Index("idx_cf_name_lower", code_fields.c.name_lower)
Index("idx_cf_class", code_fields.c.class_fqn)
Index("idx_cf_source_file", code_fields.c.source_file)

code_calls = Table(
    "code_calls", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("call_id", String(64), default=""),             # jar 来源 id, 仅溯源用
    Column("lang", String(16), nullable=False, default="java"),
    Column("caller_method_fqn", String(1024), nullable=False),
    Column("callee_class_fqn", String(512), default=""),
    Column("callee_method_name", String(256), default=""),
    Column("callee_signature", String(1024), default=""),
    Column("callee_method_fqn", String(1024), default=""),
    Column("receiver_type", String(256), default=""),
    Column("dispatch_kind", String(32), default=""),
    Column("source_file", String(512), nullable=False, default=""),
    Column("line_no", Integer, default=0),
    Column("resolved", Integer, default=0),
)
Index("idx_ccall_caller", code_calls.c.caller_method_fqn)
Index("idx_ccall_callee", code_calls.c.callee_class_fqn, code_calls.c.callee_method_name)
Index("idx_ccall_callee_fqn", code_calls.c.callee_method_fqn)
Index("idx_ccall_source_file", code_calls.c.source_file)

code_references = Table(
    "code_references", metadata,
    Column("ref_id", Integer, primary_key=True, autoincrement=True),
    Column("lang", String(16), nullable=False, default="java"),
    Column("source_fqn", String(1024), nullable=False),
    Column("source_file", String(512), nullable=False),
    Column("target_fqn", String(1024), nullable=False),
    Column("target_kind", String(32), nullable=False),
    Column("ref_kind", String(32), nullable=False),
    Column("line_no", Integer, nullable=False, default=0),
    Column("column_no", Integer, nullable=False, default=0),
)
Index("idx_cr_source", code_references.c.source_fqn)
Index("idx_cr_source_file", code_references.c.source_file)
Index("idx_cr_target", code_references.c.target_fqn)
Index("idx_cr_target_kind", code_references.c.target_kind, code_references.c.target_fqn)

# 自然复合 PK -> row_id 代理 PK(merge-review 修订, F1 同类补全): 跨模块重复 FQN 世界里
# (sub,super) 可合法地锚在两个不同文件(同名类各 extends 同一超类), 增量只重解析其一
# 必撞复合 PK; 与 4 实体表同款代理 PK + 非唯一索引。
code_inheritance = Table(
    "code_inheritance", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("sub_class_fqn", String(512), nullable=False),
    Column("super_class_fqn", String(512), nullable=False),
    Column("lang", String(16), nullable=False, default="java"),
    Column("relation_type", String(16), nullable=False),   # extends|implements
    Column("source_file", String(512), nullable=False, default=""),  # 派生自 sub 的定义文件
)
Index("idx_ci_sub_super", code_inheritance.c.sub_class_fqn, code_inheritance.c.super_class_fqn)
Index("idx_ci_super", code_inheritance.c.super_class_fqn)
Index("idx_ci_source_file", code_inheritance.c.source_file)

code_table_refs = Table(
    "code_table_refs", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("method_fqn", String(1024), nullable=False),
    Column("table_name", String(256), nullable=False),
    Column("lang", String(16), nullable=False, default="java"),
    Column("db_name", String(64), default=""),
    Column("owner", String(128), default=""),
    Column("ref_kind", String(32), default=""),
    Column("source_file", String(512), nullable=False, default=""),
)
Index("idx_ctr_method", code_table_refs.c.method_fqn)
Index("idx_ctr_table", code_table_refs.c.table_name)
Index("idx_ctr_source_file", code_table_refs.c.source_file)

code_projection_meta = Table(
    "code_projection_meta", metadata,
    Column("key", String(64), primary_key=True),
    Column("value", String(512), default=""),
)

# 增量按 source_file 删旧插新的表清单(code_files 主键即 file_path, 单列出)
DATA_TABLES = (code_classes, code_methods, code_fields, code_calls,
               code_references, code_inheritance, code_table_refs)
# 非零校验两档(T9/T10 对抗 review F2; code_table_refs 豁免 —— 盲区 3 v1 预期空):
# 硬闸(空 = build 失败保旧): 任何 Java 仓必然非空的三表
NONZERO_HARD_TABLES = (code_files, code_classes, code_methods)
# 软闸(空 = 换新但 degraded 警示): 仓风格可能合法为空(无继承/字段/调用的小仓,
# 红线 #3 新客户 init 全自动, 不能假设仓形态), 硬拒会 brick 全量永远保旧死循环
NONZERO_SOFT_TABLES = (code_fields, code_calls, code_references, code_inheritance)


def create_all(engine: Engine) -> None:
    metadata.create_all(engine, checkfirst=True)


def drop_all(engine: Engine) -> None:
    metadata.drop_all(engine, checkfirst=True)


def ensure_projection_schema(engine: Engine) -> bool:
    """版本闸门(spec §4 schema 演进策略): 投影可丢弃重建, 不走列级迁移。
    schema_version 不符 -> DROP 全部 code_* + 重建。返回是否发生了重建。
    首建(表刚建出来, 无版本行)写入当前版本, 返回 False。"""
    create_all(engine)
    with engine.connect() as conn:
        ver = conn.execute(select(code_projection_meta.c.value).where(
            code_projection_meta.c.key == "schema_version")).scalar()
    if ver == PROJECTION_SCHEMA_VERSION:
        return False
    if ver is None:
        with engine.begin() as conn:
            conn.execute(insert(code_projection_meta),
                         [{"key": "schema_version", "value": PROJECTION_SCHEMA_VERSION}])
        return False
    drop_all(engine)
    create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(code_projection_meta),
                     [{"key": "schema_version", "value": PROJECTION_SCHEMA_VERSION}])
    return True
