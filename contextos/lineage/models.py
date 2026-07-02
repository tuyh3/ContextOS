"""管道内部数据模型 — 各层间传递。移植 LP scripts/lineage/models.py + branch_detected。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceFile:
    """Layer 2 输出: 发现的源文件。"""
    path: str          # 相对 repo_root
    language: str      # "sql" / "java"
    module: str        # 路径第一层目录(billing/order/crm)
    category: str      # sql: dao_sql / db_script / other_sql; java: java
    content: str


@dataclass
class RecoveredSqlCandidate:
    """Layer 3-4 输出: 从源码恢复的候选 SQL。"""
    source_path: str
    line_start: int
    line_end: int
    container: str         # 类.方法(.sql 文件为空)
    sql_text: str
    recovery_mode: str     # literal/concat/string_builder/local_var/static_const/sql_file/semicolon_split
    placeholders: list[str] = field(default_factory=list)
    confidence: str = "medium"     # high / medium / low
    branch_detected: bool = False  # §9.3: if/else/switch/for 内 append -> 不产 edge


@dataclass
class ParsedRelation:
    """Layer 6 输出: sqlglot 解析出的一条表间关系。"""
    src_table: str
    src_col: str = ""
    dst_table: str = ""
    dst_col: str = ""
    relation_type: str = "JOIN"  # 8 取值见 01 §3.0.1
    lineage_type: str = ""       # DIRECT / INDIRECT
    src_schema: str = ""
    dst_schema: str = ""
    is_write_target: bool = False


@dataclass
class SequenceRef:
    """Layer 6 输出: Sequence 引用。"""
    sequence_name: str
    ref_type: str = "NEXTVAL"  # NEXTVAL / CURRVAL
    context_table: str = ""
    context_column: str = ""
