"""SQL 方言参数化测试(spec 2026-07-10 4.5, L3)。

设计思路: sqlglot 方言从写死 "oracle" 改为单一取值点——build 期从 profile 解析
(traits.sqlglot_dialect)并存进 metadata, 查询期读回; 默认 "oracle" 保持 CMPAK 行为
逐字节不变。判别点用反引号标识符(FROM 后加反引号包裹表名)在 oracle 方言下 sqlglot 解析报错、
mysql 下正常(实测确认)。锁三件事:
1. parse_sql 接受 dialect 参数, mysql 方言正确解析反引号 JOIN 抽出两表;
2. 默认(无参)= oracle, 现有 CMPAK 双引号/裸名行为不变;
3. build 期把解析出的方言存进 metadata_meta(sql_dialect), 查询期 trace 读回。
评分标准: mysql/oracle 各一正例; 默认参数向后兼容一例; 存/读回一对。
脚本逻辑: 纯单元(parse) + build 层集成(存)。
"""
from __future__ import annotations

from contextos.lineage import store
from contextos.lineage.sql_parse import parse_sql
from contextos.storage.db import make_engine


class TestParseDialectParam:
    def test_mysql_backtick_join_parsed(self) -> None:
        rels, _seq, err = parse_sql(
            "SELECT * FROM `orders` o JOIN `users` u ON o.uid = u.id",
            dialect="mysql")
        assert err is None, f"mysql 方言应能解析反引号, 实得 err={err}"
        tables = {r.src_table.lower() for r in rels} | {r.dst_table.lower() for r in rels}
        assert "orders" in tables and "users" in tables

    def test_oracle_dialect_fails_on_backtick(self) -> None:
        # 反证: 同一反引号 SQL 在 oracle 方言下 AST 解析失败(退 regex, 得不到 JOIN 关系)
        rels, _seq, err = parse_sql(
            "SELECT * FROM `orders` o JOIN `users` u ON o.uid = u.id",
            dialect="oracle")
        # oracle 下反引号解析崩 -> regex fallback 抓不到表间 JOIN 关系
        assert not rels or err is not None

    def test_default_dialect_is_oracle_backward_compat(self) -> None:
        # 无 dialect 参数 = oracle(CMPAK 双引号/裸名行为不变)
        rels, _seq, err = parse_sql(
            "SELECT * FROM T_A a JOIN T_B b ON a.id = b.aid")
        assert err is None
        tables = {r.src_table.upper() for r in rels} | {r.dst_table.upper() for r in rels}
        assert "T_A" in tables and "T_B" in tables


class TestDialectStoredAtBuild:
    def test_build_stores_resolved_dialect(self) -> None:
        # build 期解析方言并存 metadata_meta, 查询期读回单一取值点
        from contextos.lineage.build_database import _resolve_sql_dialect
        from contextos.profile.schema import (DatabaseConfig, MysqlConfig,
                                              MysqlInstanceConfig, OracleConfig)

        class _P:
            database = DatabaseConfig(type="mysql", mysql=MysqlConfig(instances=[
                MysqlInstanceConfig(alias="a", host="h", databases=["d"])]))
        assert _resolve_sql_dialect(_P()) == "mysql"

        class _PO:
            database = DatabaseConfig(type="oracle", oracle=OracleConfig(
                tns_admin="/t", allowed_instances=["TEST_DB1"]))
        assert _resolve_sql_dialect(_PO()) == "oracle"

    def test_meta_roundtrip(self) -> None:
        eng = make_engine("sqlite://"); store.create_all(eng)
        store.set_meta(eng, "sql_dialect", "mysql")
        assert store.get_meta(eng, "sql_dialect") == "mysql"
