"""MySQL 元数据 provider 测试(spec 2026-07-10 附录 D, L2)。

设计思路: 镜像 Oracle refresh_metadata 的三条硬契约(原子覆盖 / 拉失败保旧快照 /
空 databases 不动), 但查 information_schema 而非 ALL_* 视图。两类测试:
1. 单元(fake querier): 锁 SQL 形态(TABLE_SCHEMA IN 批量, 一条覆盖多库)+ 行映射
   (owner=database 名, base table=dataset_type TABLE / view=VIEW)+ 原子性
   (fake 中途抛 -> 旧快照原封不动, refreshed=False)。
2. 真库 integration(mysql_integration marker, 连不上 127.0.0.1:3306 自动 skip):
   对已灌 pak-bomc 表结构的测试库刷新, 断言 store 里 toptea/sysm 表数与真库一致、
   注释非空落库。fresh-env 纪律要求真跑路径(mock 假绿绕不过 information_schema 真形态)。
评分标准: 单元每契约独立用例; 集成断言真数(toptea 957 表+2 视图, sysm 250 表)。
脚本逻辑: 集成前置探活函数 _db_reachable() 决定 skip; 凭据 env 内设不落盘。
"""
from __future__ import annotations

import os
from typing import Any

import pytest

from contextos.lineage import store
from contextos.lineage.mysql_metadata import refresh_mysql_metadata
from contextos.storage.db import make_engine


class _FakeQuerier:
    """按 SQL 内容派发的 information_schema 应答桩(中性合成)。"""
    def __init__(self, rows_by_type: dict[str, list[dict]], fail: bool = False):
        self.rows_by_type = rows_by_type
        self.fail = fail
        self.queries: list[tuple[str, dict]] = []

    def query(self, sql: str, params: Any = None, **kw) -> list[dict]:
        p = dict(params or {})
        self.queries.append((sql, p))
        if self.fail:
            raise OSError("connection lost mid-fetch")
        # table_type 是 bind 参数(不在 SQL 文本), 按 params 派发(真库靠 bind 值区分)
        if p.get("ttype") == "VIEW":
            return self.rows_by_type.get("views", [])
        return self.rows_by_type.get("tables", [])


_FAKE_ROWS = {
    "tables": [
        {"TABLE_SCHEMA": "appdb", "TABLE_NAME": "wd_order", "TABLE_COMMENT": "工单表"},
        {"TABLE_SCHEMA": "appdb", "TABLE_NAME": "wd_user", "TABLE_COMMENT": ""},
        {"TABLE_SCHEMA": "sysm", "TABLE_NAME": "sysm_cd_alarm", "TABLE_COMMENT": "告警"},
    ],
    "views": [
        {"TABLE_SCHEMA": "appdb", "TABLE_NAME": "v_order_all", "TABLE_COMMENT": "VIEW"},
    ],
}


class TestUnitContract:
    def test_batch_query_uses_schema_in(self) -> None:
        eng = make_engine("sqlite://"); store.create_all(eng)
        q = _FakeQuerier(_FAKE_ROWS)
        refresh_mysql_metadata(q, eng, databases=["appdb", "sysm"],
                               db_alias="test_inst", now="2026-07-10T00:00:00")
        # 一条批量, 参数含两个库
        joined = " ".join(s for s, _ in q.queries).upper()
        assert "INFORMATION_SCHEMA.TABLES" in joined
        assert "TABLE_SCHEMA IN" in joined

    def test_row_mapping_owner_is_database(self) -> None:
        eng = make_engine("sqlite://"); store.create_all(eng)
        q = _FakeQuerier(_FAKE_ROWS)
        out = refresh_mysql_metadata(q, eng, databases=["appdb", "sysm"],
                                     db_alias="test_inst", now="2026-07-10T00:00:00")
        assert out["refreshed"] is True
        rows = store.all_table_metadata(eng)
        by_name = {(r["owner"], r["template_name"]): r for r in rows}
        assert ("appdb", "wd_order") in by_name
        assert by_name[("appdb", "wd_order")]["comment"] == "工单表"
        assert by_name[("appdb", "wd_order")]["dataset_type"] == "TABLE"
        assert by_name[("appdb", "v_order_all")]["dataset_type"] == "VIEW"
        assert by_name[("sysm", "sysm_cd_alarm")]["owner"] == "sysm"

    def test_empty_databases_keeps_old_snapshot(self) -> None:
        eng = make_engine("sqlite://"); store.create_all(eng)
        store.write_table_metadata(eng, [dict(owner="appdb", template_name="OLD",
                                              db_name="x", comment="", dataset_type="TABLE")])
        out = refresh_mysql_metadata(_FakeQuerier(_FAKE_ROWS), eng, databases=[],
                                     db_alias="test_inst", now="2026-07-10T00:00:00")
        assert out["refreshed"] is False and out["reason"] == "no_databases"
        assert any(r["template_name"] == "OLD" for r in store.all_table_metadata(eng))

    def test_fetch_failure_keeps_old_snapshot(self) -> None:
        eng = make_engine("sqlite://"); store.create_all(eng)
        store.write_table_metadata(eng, [dict(owner="appdb", template_name="OLD",
                                              db_name="x", comment="", dataset_type="TABLE")])
        out = refresh_mysql_metadata(_FakeQuerier(_FAKE_ROWS, fail=True), eng,
                                     databases=["appdb"], db_alias="test_inst",
                                     now="2026-07-10T00:00:00")
        assert out["refreshed"] is False and "OSError" in out["reason"]
        # 拉失败绝不清空旧快照(原子性)
        assert any(r["template_name"] == "OLD" for r in store.all_table_metadata(eng))


# ---- 真库 integration(连不上自动 skip)----

def _db_reachable() -> bool:
    try:
        import pymysql
        c = pymysql.connect(host="127.0.0.1", port=3306, user="root",
                            password="root", connect_timeout=2)
        c.close()
        return True
    except Exception:
        return False


@pytest.mark.mysql_integration
@pytest.mark.skipif(not _db_reachable(), reason="本地测试 MySQL(127.0.0.1:3306)不可达")
class TestRealDb:
    def _client(self):
        from contextos.db_provider.mysql_client import MySqlClient
        from contextos.profile.schema import MysqlInstanceConfig
        os.environ["MYSQL_BOMC_TEST_USER"] = "root"
        os.environ["MYSQL_BOMC_TEST_PASSWORD"] = "root"
        inst = MysqlInstanceConfig(alias="bomc_test", host="127.0.0.1", port=3306,
                                   databases=["toptea", "sysm"])
        return MySqlClient(inst, allowed_aliases=["bomc_test"])

    def test_refresh_real_test_db_matches_counts(self) -> None:
        eng = make_engine("sqlite://"); store.create_all(eng)
        with self._client() as client:
            out = refresh_mysql_metadata(client, eng, databases=["toptea", "sysm"],
                                         db_alias="bomc_test", now="2026-07-10T00:00:00")
        assert out["refreshed"] is True
        rows = store.all_table_metadata(eng)
        toptea = [r for r in rows if r["owner"] == "toptea"]
        sysm = [r for r in rows if r["owner"] == "sysm"]
        # 真库实测: toptea 957 表 + 2 视图, sysm 250 表 + 0 视图
        assert len([r for r in toptea if r["dataset_type"] == "TABLE"]) == 957
        assert len([r for r in toptea if r["dataset_type"] == "VIEW"]) == 2
        assert len([r for r in sysm if r["dataset_type"] == "TABLE"]) == 250
        # 注释真落库(中文)
        assert any(r["comment"] for r in toptea), "toptea 应有表注释落库"
