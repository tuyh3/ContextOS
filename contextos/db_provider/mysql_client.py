"""MySqlClient: 协议外壳 + SQLAlchemy 内芯(spec 2026-07-10 附录 C, L2a)。

外壳纪律(与 OracleClient 对齐, 方言中立件复用 gate_common/oracle_gate):
- 构造期过白名单三串闸(fail-closed, 在凭据读取之前);
- 凭据只走 env MYSQL_<ALIAS>_USER/_PASSWORD(python-dotenv 由上游 CLI 已加载),
  绝不进 profile/连接串日志;
- query() 先过只读 SQL 闸(assert_query_is_readonly, 方言无关纯文本闸)再碰引擎。

内芯 = SQLAlchemy engine + text()(:name 绑定原生兼容, 无占位符翻译层):
- __enter__ 急连探活是 MUST(spec C.3): create_engine 是惰性的, 死库若拖到首次
  query 才炸, 会绕过 DbRouter 负缓存自愈(db_router 的 TTL 机制依赖"进门即抛",
  Oracle 客户端是急连; 冷评审 S1)。
- duck-type 契约与 OracleClient 一致: context manager + query() -> list[dict],
  DbRouter/oracle_metadata 层"任何有 .query 的对象"即插即用。
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from contextos.db_provider.dialects import get_traits
from contextos.db_provider.gate_common import (
    DbSafetyError,
    assert_instance_is_test_only,
)
from contextos.db_provider.oracle_gate import assert_query_is_readonly
from contextos.profile.schema import MysqlInstanceConfig


class MySqlClient:
    def __init__(
        self,
        instance: MysqlInstanceConfig,
        allowed_aliases: Iterable[str],
        *,
        engine_factory: Callable[[str], Any] | None = None,
    ) -> None:
        # 闸门先行(F.2 双执行点之二: 客户端构造; 之一在 profile validator)
        assert_instance_is_test_only(
            alias=instance.alias, host=instance.host,
            databases=instance.databases, allowed_aliases=allowed_aliases,
        )
        self.instance = instance
        self._traits = get_traits("mysql")
        env_key = instance.alias.upper()
        user = os.getenv(f"MYSQL_{env_key}_USER")
        password = os.getenv(f"MYSQL_{env_key}_PASSWORD")
        if not user or not password:
            raise DbSafetyError(
                f"missing credentials for instance {instance.alias!r}: set "
                f"MYSQL_{env_key}_USER / MYSQL_{env_key}_PASSWORD in env (.env)"
            )
        self._user = user
        self._password = password
        self._engine_factory = engine_factory or self._default_engine
        self._engine: Any = None

    # -- 内芯 --
    def _url(self) -> str:
        from sqlalchemy.engine import URL
        return URL.create(
            "mysql+pymysql",
            username=self._user, password=self._password,
            host=self.instance.host, port=self.instance.port,
            # 连接落在第一个业务库; 元数据层跨库走 information_schema(TABLE_SCHEMA IN)
            database=self.instance.databases[0],
            query={"charset": "utf8mb4"},
        ).render_as_string(hide_password=False)

    def _default_engine(self, url: str) -> Any:
        from sqlalchemy import create_engine
        return create_engine(
            url,
            connect_args={"connect_timeout": self.instance.connect_timeout_seconds},
            pool_pre_ping=True,
        )

    def __enter__(self) -> "MySqlClient":
        self._engine = self._engine_factory(self._url())
        # C.3 MUST: 急连探活——失败必须在进门处抛(保 DbRouter 负缓存契约)
        conn = self._engine.connect()
        try:
            pass
        finally:
            conn.close()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def query(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        *,
        max_rows: int | None = None,
    ) -> list[dict]:
        assert_query_is_readonly(sql)   # 闸前不触引擎
        if self._engine is None:
            raise DbSafetyError("MySqlClient not entered; use `with client:`")
        if max_rows is not None:
            sql = self._traits.wrap_limit(sql, max_rows)
        from sqlalchemy import text
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), dict(params or {}))
            return [dict(r._mapping) for r in rows]


def connect_mysql_from_profile(profile: Any, alias: str | None = None) -> MySqlClient:
    """按 profile 取白名单 MySQL 实例客户端(统一取值点 profile.database)。

    返回未进门的客户端(与 connect_from_profile 的 Oracle 语义一致):
    真连发生在 `with client:` 的 __enter__(急连探活)。
    """
    db = getattr(profile, "database", None)
    if db is None or db.type != "mysql" or db.mysql is None:
        raise DbSafetyError(
            f"profile database.type={getattr(db, 'type', None)!r} is not 'mysql'; "
            "MySQL client channel unavailable"
        )
    instances = db.mysql.instances
    aliases = [i.alias for i in instances]
    if alias is None:
        selected = instances[0]
    else:
        matched = [i for i in instances if i.alias == alias]
        if not matched:
            raise DbSafetyError(
                f"alias {alias!r} not in configured mysql instances {aliases!r}"
            )
        selected = matched[0]
    return MySqlClient(selected, aliases)
