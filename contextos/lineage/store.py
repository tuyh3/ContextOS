"""血缘表存储层(红线 #6: SQLAlchemy Core 逻辑契约, 非裸 sqlite3)。

表 schema 借鉴 LP lineage_out/*.csv + sql_templates.jsonl + 01 §3.0.1。
table_metadata / table_synonyms / table_fks 由 oracle_metadata 层填充;
build 期可空(离线降级: NameResolver 只做 Profile 归一, validate 不丢边)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean, Column, Integer, MetaData, String, Table, Text, delete, func, insert, select,
)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.sql.schema import ColumnDefault

metadata = MetaData()

lineage_edges = Table(
    "lineage_edges", metadata,
    Column("edge_id", String(32), primary_key=True),
    Column("src_db", String(64), default=""),
    Column("src_owner", String(64), default=""),
    Column("src_table", String(128), default=""),
    Column("src_col", String(128), default=""),
    Column("dst_db", String(64), default=""),
    Column("dst_owner", String(64), default=""),
    Column("dst_table", String(128), default=""),
    Column("dst_col", String(128), default=""),
    Column("relation_type", String(32), default=""),
    Column("lineage_type", String(16), default=""),
    Column("src_dataset_type", String(16), default="TABLE"),
    Column("dst_dataset_type", String(16), default="TABLE"),
    Column("confidence", String(8), default="medium"),
    Column("evidence_count", Integer, default=0),
    Column("recovery_mode", String(24), default=""),
    Column("branch_detected", Boolean, default=False),
    # Block 1a: 来源标识(SQL 静态血缘 vs OBJECT_DEPENDENCY 对象依赖)。
    Column("edge_kind", String(16), default="SQL"),
    # Block 1a: 生命周期列(增量底座, §8.5)。v1 简单填值, 真正增量 build 留 v1.x。
    Column("first_seen_at", String(32), default=""),
    Column("last_seen_at", String(32), default=""),
    Column("is_active", Boolean, default=True),
    Column("source_fingerprint", String(64), default=""),
)

lineage_evidence = Table(
    "lineage_evidence", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("edge_id", String(32), index=True),
    Column("evidence_type", String(16), default=""),
    Column("evidence_ref", String(512), default=""),   # source_path:line
    Column("excerpt", Text, default=""),
    Column("extractor_version", String(16), default=""),
)

sql_templates = Table(
    "sql_templates", metadata,
    Column("template_id", String(16), primary_key=True),
    Column("source_file", String(512), default=""),
    Column("container", String(256), default=""),       # 类.方法(.sql 为空)
    Column("sql_text", Text, default=""),
    Column("recovery_mode", String(24), default=""),
    Column("confidence", String(8), default="low"),
)

unresolved_sql = Table(
    "unresolved_sql", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_path", String(512), default=""),
    Column("line_start", Integer, default=0),
    Column("recovery_mode", String(24), default=""),
    Column("reason", String(256), default=""),
    Column("sql_excerpt", Text, default=""),
)

# --- Oracle live 元数据(Task 11 填充, 空=离线降级) ---
# 裁决 5: 身份锚 = owner.table -> 复合 PK (owner, template_name), 同名表跨 schema 各存一行
# (Finding #1 修: 原单列 template_name PK 多 owner 同名表崩 UNIQUE / refresh 静默丢)。
table_metadata = Table(
    "table_metadata", metadata,
    Column("owner", String(64), primary_key=True, default=""),
    Column("template_name", String(128), primary_key=True),
    Column("db_name", String(64), default=""),
    Column("comment", Text, default=""),
    Column("dataset_type", String(16), default="TABLE"),  # TABLE / VIEW
)

table_synonyms = Table(
    "table_synonyms", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("synonym_name", String(128), index=True),
    Column("db_name", String(64), default=""),
    Column("table_owner", String(64), default=""),
    Column("table_name", String(128), default=""),
    Column("db_link", String(64), default=""),
)

table_fks = Table(
    "table_fks", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("table_a", String(128), index=True),
    Column("table_b", String(128), index=True),
)

# --- Block 1a: 对象元数据表(Oracle NUMBER 用 String 存避免溢出, LP 用 Decimal 同理) ---
columns = Table(
    "columns", metadata,
    Column("owner", String(64), primary_key=True, default=""),
    Column("table_name", String(128), primary_key=True),
    Column("column_name", String(128), primary_key=True),
    Column("data_type", String(64), default=""),
    Column("nullable", String(1), default="Y"),
    Column("comment", Text, default=""),
    Column("column_id", Integer, default=0),
    Column("db_name", String(64), default=""),
)

indexes = Table(
    "indexes", metadata,
    Column("owner", String(64), primary_key=True, default=""),
    Column("index_name", String(128), primary_key=True),
    Column("table_name", String(128), index=True),
    Column("uniqueness", String(16), default=""),
    Column("column_list", Text, default=""),       # LISTAGG 列名, 逗号分隔
    Column("db_name", String(64), default=""),
)

constraints = Table(
    "constraints", metadata,
    Column("owner", String(64), primary_key=True, default=""),
    Column("constraint_name", String(128), primary_key=True),
    Column("table_name", String(128), index=True),
    Column("constraint_type", String(2), default=""),   # P/U/C/R
    Column("r_owner", String(64), default=""),
    Column("r_constraint_name", String(128), default=""),
    Column("search_condition", Text, default=""),
    Column("db_name", String(64), default=""),
)

sequences = Table(
    "sequences", metadata,
    Column("owner", String(64), primary_key=True, default=""),
    Column("sequence_name", String(128), primary_key=True),
    Column("min_value", String(40), default=""),    # NUMBER(28) -> String 防溢出
    Column("max_value", String(40), default=""),
    Column("increment_by", String(40), default=""),
    Column("last_number", String(40), default=""),
    Column("cache_size", String(40), default=""),
    Column("cycle_flag", String(1), default="N"),
    Column("db_name", String(64), default=""),
)

views = Table(
    "views", metadata,
    Column("owner", String(64), primary_key=True, default=""),
    Column("view_name", String(128), primary_key=True),
    Column("comment", Text, default=""),            # 只存名 + 注释, 不存 TEXT(避开 K2)
    Column("db_name", String(64), default=""),
)

procedures = Table(
    "procedures", metadata,
    Column("owner", String(64), primary_key=True, default=""),
    Column("object_name", String(128), primary_key=True),
    Column("object_type", String(24), default=""),  # PROCEDURE/FUNCTION/PACKAGE
    Column("db_name", String(64), default=""),
)

dependencies = Table(
    "dependencies", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("owner", String(64), index=True),
    Column("name", String(128), index=True),
    Column("type", String(24), default=""),                 # 引用方对象类型: VIEW/PROCEDURE/TRIGGER/...
    Column("referenced_owner", String(64), index=True),
    Column("referenced_name", String(128), index=True),
    Column("referenced_type", String(24), default=""),      # 被引用方: TABLE/VIEW/SEQUENCE/...
    Column("referenced_link_name", String(128), default=""),  # dblink(Block 1b 解析)
    Column("db_name", String(64), default=""),
)

# --- Block 1b: dblink 表 + unresolved_dblinks 表 ---
dblinks = Table(
    "dblinks", metadata,
    Column("owner", String(64), primary_key=True, default=""),
    Column("db_link", String(128), primary_key=True),
    Column("host", String(512), default=""),          # TNS 别名 / 描述符 / EZConnect 串
    Column("username", String(64), default=""),
    Column("created", String(32), default=""),
    Column("db_name", String(64), default=""),         # 该 dblink 所在的本地库(来源库)
)

unresolved_dblinks = Table(
    "unresolved_dblinks", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("db_link", String(128), index=True),
    Column("host", String(512), default=""),
    Column("reason", String(64), default=""),          # no_matching_instance / unparseable_host
    Column("db_name", String(64), default=""),
)

metadata_meta = Table(
    "metadata_meta", metadata,
    Column("key", String(64), primary_key=True),
    Column("value", String(128), default=""),
)

# --- Block 1b: owner -> TNS 路由映射(多库 refresh 自管 clear+repopulate) ---
# 不进 _DATA_TABLES / _OBJECT_META_TABLES, 防单库 clear_all / clear_object_metadata 误清路由。
owner_routing = Table(
    "owner_routing", metadata,
    Column("owner", String(64), primary_key=True),
    Column("tns", String(128), default=""),       # 该 owner 元数据加载来源实例(查询期路由用)
)

_DATA_TABLES = [lineage_edges, lineage_evidence, sql_templates, unresolved_sql, unresolved_dblinks]
_META_TABLES = [table_metadata, table_synonyms, table_fks]
_OBJECT_META_TABLES = [columns, indexes, constraints, sequences, views, procedures, dependencies, dblinks]


def create_all(engine: Engine) -> None:
    # ensure_schema: 建缺失表 + 给已存在的老表补齐模型后加的列(附加式迁移)。
    # 不能只 metadata.create_all(checkfirst=True) —— 对已存在表是 no-op, 跨版本持久库
    # (如 Block 1a 之前建的 lineage_edges 缺 edge_kind 等列)会在 write 时崩。见 storage/migrate.py。
    from contextos.storage.migrate import ensure_schema
    ensure_schema(engine, metadata)


def existing_tables(engine: Engine, *names: str) -> set[str]:
    """返回 names 中真实存在于库里的表名集合。

    fresh 环境(如只跑过 init --only code)血缘表族可能整个缺失; 读路径查询前用本函数
    判存在, 缺表按"空血缘"降级, 不裸抛 OperationalError。走 SQLAlchemy inspector
    (sqlite/信创 PG 通用), 不做 "no such table" 这类方言字符串匹配(同 meta.py
    code_projection 先例)。不缓存: init 后建表, 常驻 server 下一次调用即可见。
    """
    insp = sa_inspect(engine)
    return {n for n in names if insp.has_table(n)}


def _scalar_default(column: Column) -> Any:
    """列的 Python-side 标量默认值; 无标量默认(autoincrement PK / 无 default 列)返回 None。

    只取 is_scalar 默认(纯字面量); 可调用 / server_default 不在此填(让 DB 处理),
    避免在 caller 缺省时塞错值。
    """
    d = column.default
    if isinstance(d, ColumnDefault) and d.is_scalar:
        return d.arg
    return None


def _insert_rows(engine: Engine, table: Table, rows: list[dict[str, Any]]) -> None:
    """批量 insert; 按行归一 keys 防混批静默覆盖 / 硬崩。

    根因: conn.execute(insert(table), rows) 编译成单条 executemany, 语句的 bind 参数
    取自首行(或并集首现)keys。若同一 batch 里 caller 对某 optional 列有的传有的不传
    (edge_kind/生命周期列即此设计现实), 旧路径要么用首行 keys 把后续行的显式值丢掉
    (静默数据损坏), 要么因 keys 不齐抛 InvalidRequestError(硬崩)。

    解法: 取所有行 keys 的并集 ∪ 所有带标量默认的列, 每行按这个统一 key 集补齐 ——
    缺省列填该列 Python-side 标量默认, 无默认列填 None。保证 executemany 语句的 bind
    参数稳定且每行显式值都保留。是按类修(覆盖每张表 / 每个 optional 列), 非只修 edge_kind。
    """
    if not rows:
        return
    with engine.begin() as conn:
        _insert_rows_conn(conn, table, rows)


def _normalize_rows(table: Table, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按行 keys 并集 ∪ 带标量默认的列归一(见 _insert_rows docstring 根因/解法)。"""
    provided_keys: set[str] = set()
    for row in rows:
        provided_keys.update(row.keys())
    default_keys = {c.name for c in table.columns if c.default is not None}
    uniform_keys = provided_keys | default_keys
    col_by_name = {c.name: c for c in table.columns}
    return [
        {
            k: row[k] if k in row else _scalar_default(col_by_name[k]) if k in col_by_name else None
            for k in uniform_keys
        }
        for row in rows
    ]


def _insert_rows_conn(conn: Any, table: Table, rows: list[dict[str, Any]]) -> None:
    """在已打开的 connection 上批量 insert(归一同 _insert_rows)。

    供 replace_metadata / replace_object_metadata 把 clear + 多表 write + set_meta 串进
    同一个 engine.begin() 事务, 任一步抛 -> 整体回滚, 旧快照原封不动(HIGH-2 原子性)。"""
    if not rows:
        return
    conn.execute(insert(table), _normalize_rows(table, rows))


def _all(engine: Engine, table: Table) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(select(table))]


def write_edges(engine, rows): _insert_rows(engine, lineage_edges, rows)
def write_evidence(engine, rows): _insert_rows(engine, lineage_evidence, rows)
def write_templates(engine, rows): _insert_rows(engine, sql_templates, rows)
def write_unresolved(engine, rows): _insert_rows(engine, unresolved_sql, rows)
def write_table_metadata(engine, rows): _insert_rows(engine, table_metadata, rows)
def write_table_synonyms(engine, rows): _insert_rows(engine, table_synonyms, rows)
def write_table_fks(engine, rows): _insert_rows(engine, table_fks, rows)

def all_edges(engine): return _all(engine, lineage_edges)
def all_evidence(engine): return _all(engine, lineage_evidence)
def all_templates(engine): return _all(engine, sql_templates)
def all_table_metadata(engine): return _all(engine, table_metadata)
def all_synonyms(engine): return _all(engine, table_synonyms)
def all_fks(engine): return _all(engine, table_fks)

# --- Block 1a: 对象元数据 write/all helper ---
def write_columns(engine, rows): _insert_rows(engine, columns, rows)
def write_indexes(engine, rows): _insert_rows(engine, indexes, rows)
def write_constraints(engine, rows): _insert_rows(engine, constraints, rows)
def write_sequences(engine, rows): _insert_rows(engine, sequences, rows)
def write_views(engine, rows): _insert_rows(engine, views, rows)
def write_procedures(engine, rows): _insert_rows(engine, procedures, rows)
def write_dependencies(engine, rows): _insert_rows(engine, dependencies, rows)

# --- Block 1b: dblinks + unresolved_dblinks helper ---
def write_dblinks(engine, rows): _insert_rows(engine, dblinks, rows)
def all_dblinks(engine): return _all(engine, dblinks)
def write_unresolved_dblinks(engine, rows): _insert_rows(engine, unresolved_dblinks, rows)
def all_unresolved_dblinks(engine): return _all(engine, unresolved_dblinks)

def all_columns(engine): return _all(engine, columns)
def all_indexes(engine): return _all(engine, indexes)
def all_constraints(engine): return _all(engine, constraints)
def all_sequences(engine): return _all(engine, sequences)
def all_views(engine): return _all(engine, views)
def all_procedures(engine): return _all(engine, procedures)
def all_dependencies(engine): return _all(engine, dependencies)


def evidence_for(engine: Engine, edge_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(lineage_evidence).where(lineage_evidence.c.edge_id == edge_id)
        )
        return [dict(r._mapping) for r in rows]


def count_unresolved(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(unresolved_sql)).scalar() or 0)


def has_metadata(engine: Engine) -> bool:
    """元数据表是否非空(决定 NameResolver 是否做 synonym/owner 推断 + validate 是否丢边)。"""
    with engine.connect() as conn:
        n = conn.execute(select(func.count()).select_from(table_metadata)).scalar() or 0
    return int(n) > 0


def clear_all(engine: Engine) -> None:
    """全量重建前清空所有数据表(元数据表不清, 由 oracle 层独立管理)。"""
    with engine.begin() as conn:
        for table in _DATA_TABLES:
            conn.execute(delete(table))


def clear_metadata(engine: Engine) -> None:
    with engine.begin() as conn:
        for table in _META_TABLES:
            conn.execute(delete(table))


def clear_object_metadata(engine: Engine) -> None:
    """清空 8 张对象元数据表(columns/indexes/constraints/sequences/views/procedures/dependencies/dblinks)。

    refresh_object_metadata 全量覆盖前调, 独立于 clear_metadata。
    dblinks 写回路径已在 refresh_object_metadata 实现(Task 6 补齐): clear 后由 write_dblinks 重填。
    """
    with engine.begin() as conn:
        for table in _OBJECT_META_TABLES:
            conn.execute(delete(table))


def clear_object_edges(engine: Engine) -> None:
    """只清 edge_kind=OBJECT_DEPENDENCY 的边(build_object_lineage 重建前调, 不碰静态 SQL 边)。"""
    with engine.begin() as conn:
        conn.execute(delete(lineage_edges).where(lineage_edges.c.edge_kind == "OBJECT_DEPENDENCY"))


def clear_object_unresolved_dblinks(engine: Engine) -> None:
    """只清 reason='object_dep_unresolved' 的行(build_object_lineage 重建前调, 与 clear_object_edges 对称)。

    这样 build_object_lineage 独立重调不会因跳过 clear_all 而积累重复 unresolved_dblinks 行。
    """
    with engine.begin() as conn:
        conn.execute(
            delete(unresolved_dblinks).where(
                unresolved_dblinks.c.reason == "object_dep_unresolved"
            )
        )


def _set_meta_conn(conn: Any, key: str, value: str) -> None:
    """在已打开 connection 上 upsert 一个元信息 kv(供 replace_* 纳入同一事务)。"""
    conn.execute(delete(metadata_meta).where(metadata_meta.c.key == key))
    conn.execute(insert(metadata_meta), [{"key": key, "value": value}])


def set_meta(engine: Engine, key: str, value: str) -> None:
    """upsert 一个元信息 kv(如 metadata_refreshed_at)。delete+insert 保证可移植。"""
    with engine.begin() as conn:
        _set_meta_conn(conn, key, value)


def replace_metadata(engine: Engine, *, tables: list[dict[str, Any]],
                     synonyms: list[dict[str, Any]], fks: list[dict[str, Any]],
                     owner_tns: dict[str, str] | None, refreshed_at: str) -> None:
    """原子全量覆盖表级元数据(HIGH-2): clear 3 表 + write + (owner_tns 给则覆盖 owner_routing)
    + 盖 metadata_refreshed_at, 全在单个 engine.begin() 事务内。任一步抛 -> 整体回滚, 旧快照
    原封不动(满足'写失败也绝不破坏旧快照')。

    owner_tns=None -> 不碰 owner_routing(单库 refresh_metadata 路径; 路由只由多库 refresh 管)。
    owner_tns={...} -> 覆盖 owner_routing(多库 refresh_metadata_multi 路径)。"""
    with engine.begin() as conn:
        for table in _META_TABLES:
            conn.execute(delete(table))
        _insert_rows_conn(conn, table_metadata, tables)
        _insert_rows_conn(conn, table_synonyms, synonyms)
        _insert_rows_conn(conn, table_fks, fks)
        if owner_tns is not None:
            conn.execute(delete(owner_routing))
            rows = [{"owner": (o or "").upper(), "tns": t} for o, t in owner_tns.items() if o]
            if rows:
                conn.execute(insert(owner_routing), rows)
        _set_meta_conn(conn, "metadata_refreshed_at", refreshed_at)


def replace_object_metadata(engine: Engine, *, columns: list[dict[str, Any]],
                            indexes: list[dict[str, Any]], constraints: list[dict[str, Any]],
                            sequences: list[dict[str, Any]], views: list[dict[str, Any]],
                            procedures: list[dict[str, Any]], dependencies: list[dict[str, Any]],
                            dblinks: list[dict[str, Any]], refreshed_at: str) -> None:
    """原子全量覆盖 8 张对象元数据表(HIGH-2): clear + 8 表 write + 盖 object_metadata_refreshed_at,
    全在单个 engine.begin() 事务内。任一步抛 -> 整体回滚, 旧快照原封不动。"""
    # row_sets 与 _OBJECT_META_TABLES 同序(columns/indexes/constraints/sequences/views/
    # procedures/dependencies/dblinks); 参数名遮蔽了同名模块级 Table, 故用 _OBJECT_META_TABLES
    # 引 Table 对象、参数名引行集, zip 配对, 不靠 table.name 字符串。
    row_sets = [columns, indexes, constraints, sequences, views, procedures, dependencies, dblinks]
    with engine.begin() as conn:
        for table in _OBJECT_META_TABLES:
            conn.execute(delete(table))
        for table, rows in zip(_OBJECT_META_TABLES, row_sets):
            _insert_rows_conn(conn, table, rows)
        _set_meta_conn(conn, "object_metadata_refreshed_at", refreshed_at)


def get_meta(engine: Engine, key: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(metadata_meta.c.value).where(metadata_meta.c.key == key)
        ).first()
    return row[0] if row else None


# --- Block 1b: owner_routing helper ---

def set_owner_routing(engine: Engine, mapping: dict[str, str]) -> None:
    """全量覆盖 owner -> TNS 路由映射(幂等; 内部先 delete 再 insert, 无需上层调 clear_owner_routing)。"""
    rows = [{"owner": (o or "").upper(), "tns": t} for o, t in mapping.items() if o]
    with engine.begin() as conn:
        conn.execute(delete(owner_routing))
        if rows:
            conn.execute(insert(owner_routing), rows)


def all_owner_routing(engine: Engine) -> dict[str, str]:
    return {r["owner"]: r["tns"] for r in _all(engine, owner_routing)}


def clear_owner_routing(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(owner_routing))
