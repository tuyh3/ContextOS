"""Impact Map 枚举 SSOT — 所有 Literal 取值定义,无依赖。

每组枚举出处见 v1/01-Impact-Map输出格式/design.md 对应章节。
开放枚举(Source / KnownLimitation)只声明 KNOWN_*_VALUES set,不限制实际写入,
但提供 IDE 提示 + 运行时 warning(由 schema.py validator 实现)。
"""
from __future__ import annotations

from typing import Literal

# §3.1 kind 19 取值(16 v1 可达 + 3 v2 占位)。
# 注:design.md §3.1 散落的 prose "13 取值" 是 2026-05-30 三维扩展前的旧数(扩展
# 加了 SQL 3 + 配置 4 = 7 个 v1 可达值);本枚举为代码 SSOT,以 19 为准。
Kind = Literal[
    # 方法维(v1 可达 — 04 + 02 + 07)
    "METHOD", "CLASS", "INTERFACE", "FIELD",
    # SQL 维(v1 可达 — 05)
    "SQL_TABLE", "SQL_COLUMN", "SQL_TEMPLATE",
    # 配置维(v1 可达 — 06)
    "CONFIG_FILE", "CONFIG_KEY", "CONFIG_TABLE", "RULE_SET",
    # 入口分类(v1 可达 — 04 facade resolver)
    "API_ENTRY", "JOB", "BATCH", "MSG",
    # v2 占位(v1 不应出现)
    "MENU", "USSD_NODE", "RULE_CLAUSE",
    # 其他
    "OTHER",
]

KIND_V1_REACHABLE: frozenset[str] = frozenset([
    "METHOD", "CLASS", "INTERFACE", "FIELD",
    "SQL_TABLE", "SQL_COLUMN", "SQL_TEMPLATE",
    "CONFIG_FILE", "CONFIG_KEY", "CONFIG_TABLE", "RULE_SET",
    "API_ENTRY", "JOB", "BATCH", "MSG",
    "OTHER",
])

KIND_V2_PLACEHOLDER: frozenset[str] = frozenset(["MENU", "USSD_NODE", "RULE_CLAUSE"])

KIND_SQL_DIMENSION: frozenset[str] = frozenset(["SQL_TABLE", "SQL_COLUMN", "SQL_TEMPLATE"])

KIND_CONFIG_DIMENSION: frozenset[str] = frozenset([
    "CONFIG_FILE", "CONFIG_KEY", "CONFIG_TABLE", "RULE_SET",
])

# §3.2 change_type 11 取值
ChangeType = Literal[
    "add_method", "modify_method",
    "add_class", "modify_class",
    "config_change",
    "db_config_change", "db_schema_change",
    "menu_flow_change",          # v2 占位
    "param_only_change",
    "no_code_change",
    "unknown",
]

# §3.0.1 SQL relation_type 8 取值(LP findings.md BBASE 实测分布)
RelationType = Literal[
    "WHERE_EQ", "JOIN", "SUBQUERY", "EXISTS",
    "INSERT_SELECT", "UPDATE_FROM", "DELETE_FROM", "MERGE",
]

LineageType = Literal["DIRECT", "INDIRECT"]

# §3.0.1 + 05 §3-§5 recovery_mode 8 取值 SSOT
# (LP java_extract.py 5 + sql_recover.py 2 + 多方言 spec E.6 mapper 摄入 1)
RecoveryMode = Literal[
    "literal", "concat", "string_builder",
    "local_var", "static_const",
    "sql_file", "semicolon_split",
    "mybatis_mapper",   # spec 2026-07-10 附录 E.6: MyBatis mapper 展开; confidence=medium
]

# §3.0.2 配置维 entity_type 3 取值(LP Phase 3 config_entities)
EntityType = Literal["file_key", "db_table", "db_key_pattern"]

# §3.0.2 配置维 source_type 4 取值(LP Phase 3 config_sources)
SourceType = Literal["file", "db_table", "api", "manual"]

# §3.0.2 配置维 bind_type 6 取值(LP Phase 3 config_bindings)
BindType = Literal["java_class", "java_method", "api", "table", "domain", "sql_template"]

BindDirection = Literal["read", "write", "both"]

# §3.0.2 配置维 bind_strategy 5 取值(含 LP D2 修复 C+B 策略)
BindStrategy = Literal[
    "exact_match",
    "annotation_prefix_match",   # C 策略(LP D2 修复)
    "semgrep_rule",              # B 策略
    "ripgrep_fallback",
    "llm_inferred",
]

# §2 dimension_status 4 取值
DimensionStatus = Literal["resolved", "partial", "deferred-v2", "not_applicable"]

# §2 dimension_quality 4 取值(质量轴, 与 dimension_status 覆盖轴正交;
# spec 2026-06-17 §5.2)。strong=已确信定位 / low_confidence=有证据但弱 /
# fallback_only=全兜底定位源(grep 命中非真定位) / not_applicable=无证据。
DimensionQuality = Literal["strong", "low_confidence", "fallback_only", "not_applicable"]

DimensionKey = Literal["method", "sql_table", "config"]

# §2 candidate_entrypoints[].kind 6 取值
EntrypointKind = Literal["API", "JOB", "MENU", "USSD", "BATCH", "MSG"]

# §4 + 08 §3.2 confidence_tier 3 取值(分桶阈值 SSOT 在 08,本枚举只定义取值)
ConfidenceTier = Literal["HIGH", "MEDIUM", "LOW"]

# §3.0.2 配置维 CONFIG_TABLE 大小分级(LP Phase 3 §4.1)
TableSizeTier = Literal["small", "large"]

SnapshotStrategy = Literal["full", "structured_summary"]

# §3.0.2 配置维 snapshot_env 4 取值
SnapshotEnv = Literal["dev", "test", "prod", "all"]

# §3 relations[].kind 取值(初始 1 类,未来可扩,无 v2 占位)
RelationKind = Literal["calls", "extends", "implements", "reads", "writes"]

# §3.3 evidence_refs[].source 是 *开放枚举*。
# 这里只提供"已知值"集合供 IDE 提示 + 验证 warning,实际接受任意字符串。
# 当 provider 注册新 source 时,加进 KNOWN_EVIDENCE_SOURCES + 改本注释。
KNOWN_EVIDENCE_SOURCES: frozenset[str] = frozenset([
    # 方法维桥(v1 5 座桥)
    "jdt-ls-workspaceSymbol", "jdt-ls-call-hierarchy",
    "rag-bi-encoder", "rag-cross-encoder",
    "dict-interface", "dict-capability",
    "llm-rerank",
    # SQL 维桥(v1 LP 整合)
    "lp-sql-recover-literal", "lp-java-extract-concat", "lp-java-extract-builder",
    "lp-mybatis-mapper2sql", "lp-sqlglot-parse", "lp-name-resolve",
    "oracle-mcp-tab-comments", "oracle-mcp-dependencies",
    # 配置维桥(v1 LP 整合)
    "lp-config-parser-properties", "lp-config-parser-yaml",
    "lp-config-parser-spring-xml", "lp-config-parser-mybatis-xml",
    "lp-semgrep-config-ref",
    "lp-bind-resolver-exact", "lp-bind-resolver-prefix", "lp-bind-resolver-semgrep-rule",
    "lp-db-config-marker", "lp-rule-scanner",
    "ripgrep-config-fallback",
    # v2+ 扩展(声明但 v1 无 provider 产出)
    "git-co-change", "git-blame",
    "llm-lua-disambiguation",
    "symbolic-execution-sql",
    # 人工
    "human-annotation",
])

# §6 known_limitations 开放枚举,这里只提供"已知值"集合
KNOWN_LIMITATION_CODES: frozenset[str] = frozenset([
    "java_stringbuilder_cross_method",
    "java_stringbuilder_branch_isolated",
    "java_static_const_unresolved",
    "mybatis_choose_branch_exclusive",
    "ocs_lua_script_field",
    "dataflow_write_side_table_missing",
    "object_dependency_blind_spots",   # Block 1a: ALL_DEPENDENCIES 动态SQL/间接依赖/读写不分
])
