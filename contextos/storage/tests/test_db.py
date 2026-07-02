"""SQLAlchemy engine factory smoke tests. StorageBackend tests land in test_backend_local_fs.py (Task 3)."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from contextos.storage.db import make_engine


def test_make_engine_sqlite_memory_returns_usable_engine() -> None:
    engine = make_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1")).scalar()
    assert result == 1


def test_make_engine_rejects_empty_url() -> None:
    with pytest.raises(ValueError, match="empty database URL"):
        make_engine("")


def test_make_engine_sets_sqlite_wal_and_busy_timeout(tmp_path) -> None:
    """SQLite 并发加固: make_engine 对 file-based sqlite 连接设 WAL(一写多读并存)
    + busy_timeout(撞锁等待非秒抛), 避免常驻读连接(MCP server)下 'database is locked'。
    (WAL 需 file DB; :memory: 不支持, 故用 tmp 文件。)"""
    engine = make_engine(f"sqlite:///{tmp_path / 'x.db'}")
    with engine.connect() as conn:
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()
        busy = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert journal is not None and str(journal).lower() == "wal"
    assert busy is not None and int(busy) == 30000
