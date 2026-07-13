"""MySqlClient 测试(spec 2026-07-10 附录 C, L2a)。

设计思路: 客户端 = "协议外壳 + SQLAlchemy 内芯"。外壳三道纪律逐一反扣:
1. 构造期白名单闸(gate_common 三串闸)fail-closed, 闸在凭据读取之前;
2. 凭据只走 env MYSQL_<ALIAS>_USER/_PASSWORD, 缺失即拒(镜像 Oracle 约定);
3. query() 先过只读 SQL 闸再碰引擎(禁写反扣不触网)。
内芯两条 MUST:
4. __enter__ 急连探活(C.3): 死库必须在进门时抛, 不许惰性拖到首次 query——
   否则 DbRouter 负缓存自愈失效(冷评审 S1);
5. max_rows 经 traits.wrap_limit 包装(LIMIT 方言)。
评分标准: 全部用注入 engine_factory 的 fake 引擎, 零真网络; 每条纪律独立用例,
fake 引擎带调用记录以断言"闸前不触引擎/进门即 ping"。
脚本逻辑: 纯单元测试; 真库 integration 轨在 L5 pak-bomc(测试库)覆盖。
"""
from __future__ import annotations

from typing import Any

import pytest

from contextos.db_provider.gate_common import DbSafetyError
from contextos.db_provider.mysql_client import MySqlClient, connect_mysql_from_profile
from contextos.profile.schema import MysqlInstanceConfig


def _inst(**over: Any) -> MysqlInstanceConfig:
    d = dict(alias="test_inst", host="127.0.0.1", port=3306,
             databases=["appdb", "appdb_ext"])
    d.update(over)
    return MysqlInstanceConfig(**d)


class _FakeConn:
    def __init__(self, rows: list[dict] | None = None):
        self._rows = rows or []
        self.executed: list[tuple[str, dict]] = []

    def execute(self, stmt: Any, params: Any = None):
        self.executed.append((str(stmt), dict(params or {})))
        class _Row:
            def __init__(self, d): self._mapping = d
        return [_Row(r) for r in self._rows]

    def close(self) -> None: ...
    def __enter__(self): return self
    def __exit__(self, *exc): return None


class _FakeEngine:
    def __init__(self, rows: list[dict] | None = None, fail_connect: bool = False):
        self.rows = rows or []
        self.fail_connect = fail_connect
        self.connect_calls = 0
        self.disposed = False
        self.last_conn: _FakeConn | None = None

    def connect(self) -> _FakeConn:
        self.connect_calls += 1
        if self.fail_connect:
            raise OSError("connection refused")
        self.last_conn = _FakeConn(self.rows)
        return self.last_conn

    def dispose(self) -> None:
        self.disposed = True


@pytest.fixture
def creds(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MYSQL_TEST_INST_USER", "u")
    monkeypatch.setenv("MYSQL_TEST_INST_PASSWORD", "p")


class TestConstructionGate:
    def test_prod_keyword_host_rejected_before_creds(self, monkeypatch) -> None:
        # 闸在凭据之前: 不设任何 env, 报错必须是 production keyword 而非缺凭据
        monkeypatch.delenv("MYSQL_TEST_INST_USER", raising=False)
        with pytest.raises(DbSafetyError, match="production keyword"):
            MySqlClient(_inst(host="db-master.corp"), allowed_aliases=["test_inst"])

    def test_alias_not_whitelisted_rejected(self, creds) -> None:
        with pytest.raises(DbSafetyError, match="not in allowed"):
            MySqlClient(_inst(), allowed_aliases=["other_inst"])

    def test_missing_credentials_rejected_with_env_name_hint(self, monkeypatch) -> None:
        monkeypatch.delenv("MYSQL_TEST_INST_USER", raising=False)
        monkeypatch.delenv("MYSQL_TEST_INST_PASSWORD", raising=False)
        with pytest.raises(DbSafetyError, match="MYSQL_TEST_INST_USER"):
            MySqlClient(_inst(), allowed_aliases=["test_inst"])


class TestEagerConnect:
    def test_enter_pings_eagerly(self, creds) -> None:
        eng = _FakeEngine()
        with MySqlClient(_inst(), allowed_aliases=["test_inst"],
                         engine_factory=lambda url: eng) as c:
            assert eng.connect_calls >= 1   # C.3 MUST: 进门即 ping
            assert isinstance(c, MySqlClient)
        assert eng.disposed is True

    def test_dead_db_raises_in_enter_not_in_query(self, creds) -> None:
        eng = _FakeEngine(fail_connect=True)
        client = MySqlClient(_inst(), allowed_aliases=["test_inst"],
                             engine_factory=lambda url: eng)
        with pytest.raises(Exception):
            client.__enter__()   # 冷评审 S1: 死库在进门抛, 保 DbRouter 负缓存

    def test_engine_url_shape(self, creds) -> None:
        seen: list[str] = []
        def factory(url: str):
            seen.append(str(url))
            return _FakeEngine()
        with MySqlClient(_inst(), allowed_aliases=["test_inst"], engine_factory=factory):
            pass
        assert seen and seen[0].startswith("mysql+pymysql://")
        assert "127.0.0.1" in seen[0] and "3306" in seen[0] and "appdb" in seen[0]


class TestQueryGate:
    def test_write_sql_rejected_without_touching_engine(self, creds) -> None:
        eng = _FakeEngine()
        with MySqlClient(_inst(), allowed_aliases=["test_inst"],
                         engine_factory=lambda url: eng) as c:
            ping_calls = eng.connect_calls
            with pytest.raises(DbSafetyError):
                c.query("UPDATE t SET a = 1")
            assert eng.connect_calls == ping_calls   # 禁写反扣不触引擎

    def test_query_returns_list_of_dicts(self, creds) -> None:
        eng = _FakeEngine(rows=[{"a": 1}, {"a": 2}])
        with MySqlClient(_inst(), allowed_aliases=["test_inst"],
                         engine_factory=lambda url: eng) as c:
            out = c.query("SELECT a FROM t WHERE x = :x", {"x": 9})
        assert out == [{"a": 1}, {"a": 2}]
        sql, params = eng.last_conn.executed[0]
        assert params == {"x": 9}

    def test_max_rows_wraps_with_limit(self, creds) -> None:
        eng = _FakeEngine()
        with MySqlClient(_inst(), allowed_aliases=["test_inst"],
                         engine_factory=lambda url: eng) as c:
            c.query("SELECT a FROM t", max_rows=50)
        sql, _ = eng.last_conn.executed[0]
        assert sql.rstrip().endswith("LIMIT 50")


class TestProfileFactory:
    def test_connect_from_mysql_profile(self, creds, make_mysql_profile) -> None:
        client = connect_mysql_from_profile(make_mysql_profile())
        assert client.instance.alias == "test_inst"

    def test_oracle_profile_rejected(self, make_oracle_profile) -> None:
        with pytest.raises(DbSafetyError, match="not 'mysql'"):
            connect_mysql_from_profile(make_oracle_profile())
