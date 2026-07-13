"""DialectTraits 方言特征表测试(spec 2026-07-10 附录 B)。

设计思路: traits 是全仓唯一的方言分支点——每方言一行声明式数据(sqlglot 方言/
标识符折叠方向/行数限制包装/系统库清单/能力矩阵)。测试锁三件事:
1. oracle/mysql 两行的每个 trait 值与 spec 附录 B 逐格一致(oracle 行=现状固化,
   改错即破坏 CMPAK);
2. 折叠方向语义(oracle=upper / mysql=lower)与 LIMIT 包装方言正确;
3. openGauss 预留行按 compat_mode 映射 sqlglot 方言(A->oracle/B->mysql/PG->postgres,
   spec 附录 I 标注的自研推断), 未知方言/未知 compat fail-closed 抛错。
评分标准: 全部断言精确值比对, 不做模糊匹配; 错一格即红。
脚本逻辑: 纯单元测试, 零 IO 零 mock。
"""
from __future__ import annotations

import pytest

from contextos.db_provider.dialects import DialectTraits, get_traits


class TestOracleRow:
    def test_oracle_traits_match_current_behavior(self) -> None:
        t = get_traits("oracle")
        assert t.sqlglot_dialect == "oracle"
        assert t.identifier_fold == "upper"
        assert t.implemented is True
        assert t.has_synonym is True
        assert t.has_sequence is True
        assert t.has_dblink is True
        assert t.object_dependency_source == "dictionary"

    def test_oracle_fold_is_upper(self) -> None:
        assert get_traits("oracle").fold_identifier("my_Table") == "MY_TABLE"

    def test_oracle_limit_wrap_uses_rownum(self) -> None:
        wrapped = get_traits("oracle").wrap_limit("SELECT * FROM T", 100)
        assert "ROWNUM" in wrapped and "100" in wrapped


class TestMysqlRow:
    def test_mysql_traits(self) -> None:
        t = get_traits("mysql")
        assert t.sqlglot_dialect == "mysql"
        assert t.identifier_fold == "lower"
        assert t.implemented is True
        assert t.has_synonym is False
        assert t.has_sequence is False
        assert t.has_dblink is False
        assert t.object_dependency_source == "view_definition"

    def test_mysql_system_schemas(self) -> None:
        assert set(get_traits("mysql").system_schemas) == {
            "information_schema", "mysql", "performance_schema", "sys",
        }

    def test_mysql_fold_is_lower(self) -> None:
        assert get_traits("mysql").fold_identifier("CCP_Coll_Info") == "ccp_coll_info"

    def test_mysql_limit_wrap_uses_limit(self) -> None:
        wrapped = get_traits("mysql").wrap_limit("SELECT * FROM t", 50)
        assert wrapped.rstrip().endswith("LIMIT 50")
        assert "ROWNUM" not in wrapped


class TestReservedRows:
    """postgres/opengauss 预留行: traits 数据在, implemented=False(L1b validator 依此拒载)。"""

    def test_postgres_reserved(self) -> None:
        t = get_traits("postgres")
        assert t.sqlglot_dialect == "postgres"
        assert t.identifier_fold == "lower"
        assert t.implemented is False
        assert t.has_sequence is True

    @pytest.mark.parametrize("compat,expected", [
        ("A", "oracle"), ("B", "mysql"), ("PG", "postgres"),
    ])
    def test_opengauss_sqlglot_dialect_follows_compat_mode(
        self, compat: str, expected: str
    ) -> None:
        t = get_traits("opengauss", compat_mode=compat)
        assert t.sqlglot_dialect == expected
        assert t.implemented is False
        assert t.has_synonym is True   # openGauss 有 PG_SYNONYM(spec 附录 B)

    def test_opengauss_unknown_compat_rejected(self) -> None:
        with pytest.raises(ValueError, match="compat_mode"):
            get_traits("opengauss", compat_mode="X")


class TestFailClosed:
    def test_unknown_dialect_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown database type"):
            get_traits("sqlserver")

    def test_traits_frozen(self) -> None:
        t = get_traits("mysql")
        with pytest.raises(Exception):
            t.sqlglot_dialect = "oracle"  # type: ignore[misc]
        assert isinstance(t, DialectTraits)
