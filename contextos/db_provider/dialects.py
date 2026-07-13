"""DialectTraits 方言特征表(spec 2026-07-10 附录 B)——全仓唯一的方言分支点。

每方言一行声明式数据。下游(lineage/config_dim/mcp)禁止散落 `if type == "mysql"`
式判断: 需要方言差异时读 traits, 加新方言 = 本表加一行 + 注册对应 provider。
postgres/opengauss 是预留行(implemented=False): traits 数据即设计依据的固化,
profile validator 据 implemented 拒载未实装类型(附录 A.1)。
openGauss 的 sqlglot 方言按库级 compat_mode 映射(A->oracle/B->mysql/PG->postgres):
这是自研推断而非社区先例(spec 附录 I), 真接入时须真样本回归验证。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

# Oracle 系统 schema 全集的 SSOT 在 profile/schema.py(_ORACLE_SYSTEM_SCHEMAS,
# 作为 tables.exclude_schemas 默认值)。traits 不复制那份长清单, 置空表示
# "沿用 profile tables namespace 现状"; 新方言(mysql/postgres)的短清单在此声明。
_ORACLE_SCHEMAS_SEE_PROFILE: tuple[str, ...] = ()


@dataclass(frozen=True)
class DialectTraits:
    name: str
    sqlglot_dialect: str
    identifier_fold: Literal["upper", "lower"]
    system_schemas: tuple[str, ...]
    implemented: bool
    has_synonym: bool
    has_sequence: bool
    has_dblink: bool
    # 对象依赖证据来源: dictionary=字典视图(Oracle ALL_DEPENDENCIES/PG pg_depend)
    # view_definition=解析视图定义文本(MySQL 5.7 无依赖字典) / none=无
    object_dependency_source: Literal["dictionary", "view_definition", "none"]

    def fold_identifier(self, name: str) -> str:
        return name.upper() if self.identifier_fold == "upper" else name.lower()

    def wrap_limit(self, sql: str, n: int) -> str:
        if self.sqlglot_dialect == "oracle":
            return f"SELECT * FROM ({sql}) WHERE ROWNUM <= {int(n)}"
        # MySQL/PG 系: 派生表必须带别名(MySQL 强制)
        return f"SELECT * FROM ({sql}) _limited LIMIT {int(n)}"


_TRAITS: dict[str, DialectTraits] = {
    "oracle": DialectTraits(
        name="oracle", sqlglot_dialect="oracle", identifier_fold="upper",
        system_schemas=_ORACLE_SCHEMAS_SEE_PROFILE, implemented=True,
        has_synonym=True, has_sequence=True, has_dblink=True,
        object_dependency_source="dictionary",
    ),
    "mysql": DialectTraits(
        name="mysql", sqlglot_dialect="mysql", identifier_fold="lower",
        system_schemas=("information_schema", "mysql", "performance_schema", "sys"),
        implemented=True,
        has_synonym=False, has_sequence=False, has_dblink=False,
        object_dependency_source="view_definition",
    ),
    "postgres": DialectTraits(
        name="postgres", sqlglot_dialect="postgres", identifier_fold="lower",
        # pg_% 前缀官方保留, 一条前缀规则覆盖 pg_catalog/pg_toast/pg_temp_N(spec 附录 I)
        system_schemas=("information_schema", "pg_catalog", "pg_toast"),
        implemented=False,
        has_synonym=False, has_sequence=True, has_dblink=False,
        object_dependency_source="dictionary",   # pg_depend 主路线(spec 附录 B)
    ),
}

# openGauss 基行: PG 系内核(9.2.4)+ 自有对象(PG_SYNONYM/LARGE SEQUENCE);
# sqlglot 方言不定, 由 compat_mode 决定, 故不进 _TRAITS 静态表。
_OPENGAUSS_BASE = DialectTraits(
    name="opengauss", sqlglot_dialect="postgres", identifier_fold="lower",
    system_schemas=("information_schema", "pg_catalog", "pg_toast"),
    implemented=False,
    has_synonym=True, has_sequence=True, has_dblink=False,
    object_dependency_source="dictionary",
)

_OPENGAUSS_COMPAT_TO_SQLGLOT = {"A": "oracle", "B": "mysql", "PG": "postgres"}


def get_traits(db_type: str, *, compat_mode: str | None = None) -> DialectTraits:
    if db_type == "opengauss":
        mode = compat_mode or "A"   # openGauss 建库默认 A(Oracle 兼容)
        if mode not in _OPENGAUSS_COMPAT_TO_SQLGLOT:
            raise ValueError(
                f"opengauss compat_mode {mode!r} not in "
                f"{sorted(_OPENGAUSS_COMPAT_TO_SQLGLOT)}"
            )
        return replace(_OPENGAUSS_BASE, sqlglot_dialect=_OPENGAUSS_COMPAT_TO_SQLGLOT[mode])
    try:
        return _TRAITS[db_type]
    except KeyError:
        raise ValueError(
            f"unknown database type {db_type!r}; known: "
            f"{sorted(_TRAITS) + ['opengauss']}"
        ) from None
