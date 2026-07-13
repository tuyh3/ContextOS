"""build_database_dimension 的 MySQL 分派测试(spec 2026-07-10 附录 D, L2 接线)。

设计思路: build_database_dimension 是 05 维总入口, 现按 profile.database.type 分派——
oracle 走既有实例遍历链, mysql 走 information_schema 元数据链。锁三件事:
1. 分派正确: mysql profile 下, table_metadata 被 MySQL 元数据填充(owner=库名),
   db_status=connected; 不触碰 Oracle 的 discover_owners/ALL_* 路径;
2. owner_routing 落库: 每个库(owner)路由到其实例 alias, 使 lookup_table 可路由;
3. 降级诚实: 连接/拉取失败 -> db_status=degraded 保旧快照, 仍跑静态血缘(与 Oracle 同纪律);
   skip_db=True -> 不连库只跑静态血缘。
真库 integration(连不上自动 skip): 对测试库真跑, 断言 store 落 957+250 表且 db_status=connected。
评分标准: 单元用注入 client 工厂(fake), 每分支独立断言; 集成断言真数。
脚本逻辑: 复用 test_build_database.py 的 profile 构造范式(改 database 段为 mysql)。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from contextos.lineage import build_database, store
from contextos.profile.schema import (
    CodeConfig, DatabaseConfig, EmbeddingConfig, IngestionConfig, JdtlsRuntimeConfig,
    LLMConfig, MysqlConfig, MysqlInstanceConfig, ProjectConfig, Profile,
    QueryExpansionConfig, RerankerConfig, StorageConfig, TablesConfig,
)
from contextos.storage.db import make_engine


def _mysql_profile(tmp_path: Path, databases: list[str]) -> Profile:
    return Profile(
        llm=LLMConfig(provider="x", api_key_env="X"),
        embedding=EmbeddingConfig(model="m"),
        reranker=RerankerConfig(model="m"),
        query_expansion=QueryExpansionConfig(translation_provider="p", fallback_provider="p"),
        storage=StorageConfig(data_dir=str(tmp_path)),
        ingestion=IngestionConfig(),
        jdtls_runtime=JdtlsRuntimeConfig(jdtls_path="/j", lombok_path="/l", java_home="/h"),
        database=DatabaseConfig(type="mysql", mysql=MysqlConfig(instances=[
            MysqlInstanceConfig(alias="bomc_test", host="127.0.0.1", port=3306,
                                databases=databases)])),
        code=CodeConfig(),
        tables=TablesConfig(),
        projects=[ProjectConfig(name="p", path=str(tmp_path), language="java")],
    )


class _FakeMysqlClient:
    """entered 后 query information_schema 的桩; 按 ttype 参数派发。"""
    def __init__(self, rows: dict[str, list[dict]], fail: bool = False):
        self.rows = rows
        self.fail = fail

    def __enter__(self):
        if self.fail:
            raise OSError("cannot connect")
        return self

    def __exit__(self, *exc): return None

    def query(self, sql: str, params: Any = None, **kw) -> list[dict]:
        p = dict(params or {})
        return self.rows.get("views" if p.get("ttype") == "VIEW" else "tables", [])


_ROWS = {
    "tables": [
        {"TABLE_SCHEMA": "toptea", "TABLE_NAME": "wd_order", "TABLE_COMMENT": "工单"},
        {"TABLE_SCHEMA": "sysm", "TABLE_NAME": "sysm_cd_alarm", "TABLE_COMMENT": "告警"},
    ],
    "views": [{"TABLE_SCHEMA": "toptea", "TABLE_NAME": "v_all", "TABLE_COMMENT": "VIEW"}],
}


class TestMysqlDispatch:
    def test_mysql_profile_populates_metadata(self, tmp_path) -> None:
        eng = make_engine("sqlite://")
        out = build_database.build_database_dimension(
            _mysql_profile(tmp_path, ["toptea", "sysm"]), eng,
            now="2026-07-10T00:00:00", repo_root=tmp_path,
            mysql_client_factory=lambda inst: _FakeMysqlClient(_ROWS))
        assert out["db_status"] == "connected"
        rows = store.all_table_metadata(eng)
        owners = {r["owner"] for r in rows}
        assert owners == {"toptea", "sysm"}
        assert any(r["dataset_type"] == "VIEW" for r in rows)

    def test_mysql_owner_routing_written(self, tmp_path) -> None:
        eng = make_engine("sqlite://")
        build_database.build_database_dimension(
            _mysql_profile(tmp_path, ["toptea", "sysm"]), eng,
            now="2026-07-10T00:00:00", repo_root=tmp_path,
            mysql_client_factory=lambda inst: _FakeMysqlClient(_ROWS))
        routing = store.all_owner_routing(eng)
        # owner(库名, 大写归一)-> 实例 alias
        assert routing.get("TOPTEA") == "bomc_test"
        assert routing.get("SYSM") == "bomc_test"

    def test_mysql_connect_failure_degraded_keeps_snapshot(self, tmp_path) -> None:
        eng = make_engine("sqlite://"); store.create_all(eng)
        store.write_table_metadata(eng, [dict(owner="toptea", template_name="OLD",
                                              db_name="x", comment="", dataset_type="TABLE")])
        out = build_database.build_database_dimension(
            _mysql_profile(tmp_path, ["toptea"]), eng,
            now="2026-07-10T00:00:00", repo_root=tmp_path,
            mysql_client_factory=lambda inst: _FakeMysqlClient(_ROWS, fail=True))
        assert out["db_status"] == "degraded"
        assert any(r["template_name"] == "OLD" for r in store.all_table_metadata(eng))
        assert "lineage" in out   # 静态血缘仍跑

    def test_skip_db_mysql_no_connect(self, tmp_path) -> None:
        eng = make_engine("sqlite://")
        called = []
        out = build_database.build_database_dimension(
            _mysql_profile(tmp_path, ["toptea"]), eng,
            now="2026-07-10T00:00:00", repo_root=tmp_path, skip_db=True,
            mysql_client_factory=lambda inst: called.append(1) or _FakeMysqlClient(_ROWS))
        assert out["db_status"] == "offline"
        assert not called   # skip_db 不连库


def _db_reachable() -> bool:
    try:
        import pymysql
        c = pymysql.connect(host="127.0.0.1", port=3306, user="root",
                            password="root", connect_timeout=2); c.close()
        return True
    except Exception:
        return False


@pytest.mark.mysql_integration
@pytest.mark.skipif(not _db_reachable(), reason="本地测试 MySQL 不可达")
def test_build_real_test_db(tmp_path) -> None:
    os.environ["MYSQL_BOMC_TEST_USER"] = "root"
    os.environ["MYSQL_BOMC_TEST_PASSWORD"] = "root"
    eng = make_engine("sqlite://")
    out = build_database.build_database_dimension(
        _mysql_profile(tmp_path, ["toptea", "sysm"]), eng,
        now="2026-07-10T00:00:00", repo_root=tmp_path)
    assert out["db_status"] == "connected"
    rows = store.all_table_metadata(eng)
    assert len([r for r in rows if r["owner"] == "toptea"]) == 959  # 957 表 + 2 视图
    assert len([r for r in rows if r["owner"] == "sysm"]) == 250
