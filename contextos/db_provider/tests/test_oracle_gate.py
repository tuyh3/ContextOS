"""Oracle gate 三道闸门反扣测试。绕过尝试必须 raise OracleSafetyError。"""
from __future__ import annotations

import pytest

from contextos.db_provider.oracle_gate import (
    OracleSafetyError,
    assert_query_is_readonly,
    assert_tns_is_test_only,
)


@pytest.mark.parametrize("tns", [
    "TEST_DB1",
    "TEST_DB2",
    "TEST_DB3",
])
def test_whitelisted_test_tns_passes(tns: str) -> None:
    assert_tns_is_test_only(tns, allowed=[tns])


@pytest.mark.parametrize("tns", [
    "PROD_PAM",
    "my_prd_db",
    "LIVE_BILLING",
    "MASTER_REPLICA",
    "RELEASE_19C",
    "test_prod_clone",
])
def test_production_keyword_rejected_even_if_whitelisted(tns: str) -> None:
    with pytest.raises(OracleSafetyError, match="production keyword"):
        assert_tns_is_test_only(tns, allowed=[tns])


def test_tns_not_in_whitelist_rejected() -> None:
    with pytest.raises(OracleSafetyError, match="not in allowed_instances"):
        assert_tns_is_test_only("SOMEOTHER_DB",
                                allowed=["TEST_DB1"])


@pytest.mark.parametrize("sql", [
    "SELECT * FROM T",
    "  select 1 from dual  ",
    "WITH x AS (SELECT 1 FROM dual) SELECT * FROM x",
    "/* comment */ SELECT 1 FROM dual",
    "-- comment\nSELECT 1 FROM dual",
    # LISTAGG ... ON OVERFLOW TRUNCATE: 'TRUNCATE' 是 LISTAGG 溢出子句关键字, 不是
    # TRUNCATE TABLE 写语句; 闸门曾按裸 \bTRUNCATE\b 误判, 误伤合法只读元数据查询。
    "SELECT LISTAGG(c, ',') WITHIN GROUP (ORDER BY c) ON OVERFLOW TRUNCATE AS L FROM T",
])
def test_readonly_sql_passes(sql: str) -> None:
    assert_query_is_readonly(sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM T",
    "UPDATE T SET A=1",
    "INSERT INTO T VALUES (1)",
    "DROP TABLE T",
    "TRUNCATE TABLE T",
    "truncate table foo",                       # 大小写不敏感: 仍拒
    "TRUNCATE CLUSTER MY_CLUSTER",              # TRUNCATE CLUSTER 写语句: 仍拒
    # 走 WITH 前缀绕过 prefix 闸门, 由关键字扫描层兜住真 TRUNCATE TABLE(纵深防御)
    "WITH x AS (SELECT 1 FROM dual) TRUNCATE TABLE T",
    "MERGE INTO T USING S ON (T.A=S.A) WHEN MATCHED THEN UPDATE SET T.A=S.A",
    "CREATE TABLE T (A INT)",
    "ALTER TABLE T ADD B INT",
    "GRANT SELECT ON T TO PUBLIC",
    "SELECT 1; DROP TABLE T",
    "WITH x AS (SELECT 1 FROM dual) DELETE FROM T",
    "BEGIN DELETE FROM T; END;",
    "EXEC PROC_NAME",
    "CALL PROC()",
])
def test_non_readonly_sql_rejected(sql: str) -> None:
    with pytest.raises(OracleSafetyError):
        assert_query_is_readonly(sql)
