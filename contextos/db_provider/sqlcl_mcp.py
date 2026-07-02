"""Oracle test-instance reader for POC.

Uses python-oracledb thin client (no Instant Client required). Enforces three safety
rails:
  1. TNS name must be in [oracle].allowed_instances whitelist (projects.toml)
  2. TNS name must not contain production keywords (PROD/PRD/LIVE/MASTER/RELEASE) even if
     it was added to whitelist by accident
  3. Every SQL query must be read-only (SELECT / WITH ... SELECT); DML and DDL refused

Credentials come from .env (loaded via python-dotenv). Never printed to logs.
"""
from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import oracledb
from dotenv import load_dotenv

from contextos.db_provider.oracle_gate import (
    OracleSafetyError,
    PRODUCTION_KEYWORDS,
    assert_query_is_readonly,
    assert_tns_is_test_only,
)

log = logging.getLogger(__name__)


@dataclass
class OracleConfig:
    tns_admin: str
    allowed_instances: list[str]
    # 连接(握手)超时秒数。短值让 oracle offline 时连接快速放弃, 避免 fan_out 逐个等满
    # oracledb 默认(几十秒级)拖死 health_check。查询超时(query_timeout_seconds)是另一回事。
    connect_timeout_seconds: int = 5


def load_oracle_config(toml_path: Path) -> OracleConfig:
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    o = data["oracle"]
    return OracleConfig(
        tns_admin=o["tns_admin"],
        allowed_instances=list(o["allowed_instances"]),
        connect_timeout_seconds=int(o.get("connect_timeout_seconds", 5)),
    )



# 某电信客户 Oracle 库字符集是 AL32UTF8(UTF-8), 但历史上混入了非 UTF-8 脏字节(实测起头 0xce,
# 多见于表/列注释这类文本列 —— 早年用不匹配的客户端/迁移把地区编码字节原样塞进了 UTF-8 库)。
# 严格 UTF-8 解码遇到这种字节会 UnicodeDecodeError, 在 fetchall 处崩掉整个元数据抓取
# (sqlcl_mcp -> lineage 元数据 fetch 降级, 连锁 config 维降级)。output type handler 对文本列用
# encoding_errors='replace': 坏字节 -> � 顶替, 其余正常返回, 不阻断元数据。非文本列(NUMBER/
# DATE/RAW 等)返回 None 走默认解析, 不受影响。
_TEXT_TYPE_CODES = frozenset({
    oracledb.DB_TYPE_VARCHAR, oracledb.DB_TYPE_CHAR, oracledb.DB_TYPE_LONG,
    oracledb.DB_TYPE_NVARCHAR, oracledb.DB_TYPE_NCHAR,
})


def _tolerant_output_handler(cursor: Any, metadata: Any) -> Any:
    """oracledb outputtypehandler(cursor, metadata): 文本列用 encoding_errors='replace' 容错脏字节。

    触发: metadata.type_code 是文本类型(见 _TEXT_TYPE_CODES)-> 返回带 encoding_errors='replace'
    的 var(坏字节 -> U+FFFD �, 其余字符完好); 非文本列返回 None 走 oracledb 默认解析。
    'replace' 而非崩: 脏字节是源库的历史数据问题(非本工具产生), 容错让元数据照常抓回,
    比整轮 fetch 崩成空快照好(损坏仅限那几个字符变 �, 是注释/证据类文本, 可接受)。
    不传 size: 实测(2026-06-07 真实客户环境)oracledb 按列元数据定 var buffer, VARCHAR2(4000) 物理
    上限 4000 字节必然装得下, 真实长注释(最长 1032 字符)0 截断 —— 故无需 size, 加 size 反而会
    给无界的 LONG 套上限。"""
    if metadata.type_code in _TEXT_TYPE_CODES:
        return cursor.var(metadata.type_code, arraysize=cursor.arraysize,
                          encoding_errors="replace")
    return None


class OracleClient:
    """Per-connection helper to a whitelisted Oracle test instance."""

    def __init__(self, tns_name: str, config: OracleConfig, user: str, password: str):
        self._enforce_whitelist(tns_name, config)
        # oracledb 4.x thin mode 不读 TNS_ADMIN 环境变量;必须用 connect(config_dir=...)。
        # 2026-05-28 Step 8.1 实测踩过这个坑(DPY-4027)。
        self._config_dir = config.tns_admin
        self._tns = tns_name
        self._user = user
        self._password = password
        self._connect_timeout = config.connect_timeout_seconds
        self._conn: oracledb.Connection | None = None

    @staticmethod
    def _enforce_whitelist(tns_name: str, config: OracleConfig) -> None:
        assert_tns_is_test_only(tns_name, allowed=config.allowed_instances)

    @classmethod
    def from_config(cls, tns_name: str, config: OracleConfig) -> OracleClient:
        """Read user/password from environment ORACLE_<TNS_NAME>_USER / _PASSWORD."""
        load_dotenv()
        user = os.getenv(f"ORACLE_{tns_name}_USER")
        password = os.getenv(f"ORACLE_{tns_name}_PASSWORD")
        if not user or not password:
            raise OracleSafetyError(
                f"Missing credentials for {tns_name} in .env "
                f"(expected ORACLE_{tns_name}_USER and ORACLE_{tns_name}_PASSWORD)"
            )
        return cls(tns_name=tns_name, config=config, user=user, password=password)

    def __enter__(self) -> OracleClient:
        self._conn = oracledb.connect(
            user=self._user,
            password=self._password,
            dsn=self._tns,
            config_dir=self._config_dir,
            tcp_connect_timeout=self._connect_timeout,
        )
        # charset 容错: 某电信客户 AL32UTF8 库混入非 UTF-8 脏字节, 严格解码会崩元数据抓取(见上)。
        self._conn.outputtypehandler = _tolerant_output_handler
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        arraysize: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run a read-only query. `arraysize` lets bulk pulls request fewer
        round-trips for very large result sets (e.g. ALL_TAB_COLUMNS WHERE OWNER=...
        on a schema with ~1M column rows). Default oracledb arraysize is 100."""
        assert_query_is_readonly(sql)
        if self._conn is None:
            raise RuntimeError("OracleClient must be used as a context manager")
        cur = self._conn.cursor()
        if arraysize is not None:
            cur.arraysize = arraysize
            cur.prefetchrows = arraysize + 1
        cur.execute(sql, params or {})
        # cur.description is None only for non-result statements (DML/DDL),
        # which assert_query_is_readonly already refused — but narrow it for pyright.
        if cur.description is None:
            return []
        cols: list[str] = [str(d[0]) for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def connect_from_profile(profile: object, tns: str | None = None) -> OracleClient:
    """Return an OracleClient for a whitelisted Oracle test instance via Profile.

    Honors three safety rails (whitelist + prod-keyword block + read-only SQL)
    delegated to oracle_gate.assert_tns_is_test_only and OracleClient._enforce_whitelist.
    Credentials are read from environment variables (ORACLE_<TNS>_USER / _PASSWORD)
    as with OracleClient.from_config — never embedded in Profile.

    Usage (context manager required to open the connection)::

        with connect_from_profile(profile) as client:
            rows = client.query("SELECT 1 FROM dual")
    """
    from contextos.profile.schema import Profile

    if not isinstance(profile, Profile):
        raise TypeError(f"expected Profile, got {type(profile).__name__}")
    selected = tns or profile.oracle.allowed_instances[0]
    assert_tns_is_test_only(selected, allowed=profile.oracle.allowed_instances)
    cfg = OracleConfig(
        tns_admin=profile.oracle.tns_admin,
        allowed_instances=list(profile.oracle.allowed_instances),
        connect_timeout_seconds=profile.oracle.connect_timeout_seconds,
    )
    return OracleClient.from_config(tns_name=selected, config=cfg)
