"""SQLAlchemy engine factory."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event


def _enable_sqlite_concurrency(engine: Engine) -> None:
    """SQLite 并发加固: WAL(一写多读并存)+ busy_timeout(撞锁等待而非秒抛)。

    默认 rollback-journal 模式下, 写者提交要拿独占锁, 只要还有读连接(如常驻
    MCP server)攥着读锁就冲突 -> `database is locked`。WAL 让一写多读并存,
    busy_timeout 把瞬时争用从"秒抛"改成"等一会"。

    只对 sqlite 方言生效, 不影响信创 PG 抽象层(红线 #6)。
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
        finally:
            cur.close()


def make_engine(url: str) -> Engine:
    if not url:
        raise ValueError("empty database URL passed to make_engine")
    engine = create_engine(url, future=True, pool_pre_ping=True)
    _enable_sqlite_concurrency(engine)
    return engine


def engine_from_profile(profile: object) -> Engine:
    from contextos.profile.schema import Profile

    if not isinstance(profile, Profile):
        raise TypeError(f"expected Profile, got {type(profile).__name__}")
    data_dir = Path(profile.storage.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{data_dir / 'contextos.db'}"
    return make_engine(url)
