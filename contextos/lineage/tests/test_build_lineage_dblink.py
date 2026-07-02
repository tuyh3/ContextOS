"""Task 1 (Block 2): build_lineage dblink_index pass-through test.

Design intent:
  - NameResolver already supports dblink_index since Block 1b, but build_lineage
    never forwarded it, so cross-db edges via @DBLINK notation in .sql files were
    silently dropped (db field left empty, no db enrichment).
  - These tests verify the fix: build_lineage now accepts and forwards dblink_index.

Scoring / pass criteria:
  1. test_build_lineage_resolves_code_dblink_to_cross_db_edge:
     A .sql file with "FROM CB_BILL@BILLING" must produce at least one edge where
     CB_BILL's db side is resolved to TEST_DB3 (the mapped target in dblink_index).
  2. test_build_lineage_dblink_none_keeps_old_behavior:
     When dblink_index is not passed (None), edges still appear but db fields are
     empty (no enrichment), matching pre-Block-1b behavior for offline mode.

Test logic (automated):
  - Both tests use an in-memory SQLite engine + tmp_path .sql fixture.
  - store.all_edges() returns dicts with src_db/dst_db; assertions check those fields.
"""
from contextos.lineage import store
from contextos.lineage.pipeline import build_lineage
from contextos.profile.schema import CodeConfig, DaoSqlPattern, TablesConfig
from contextos.storage.db import make_engine


def test_build_lineage_resolves_code_dblink_to_cross_db_edge(tmp_path):
    # A DAO .sql: local table INSERT ... SELECT from cross-db table (via dblink BILLING)
    sql_dir = tmp_path / "impl"
    sql_dir.mkdir()
    (sql_dir / "Foo.sql").write_text(
        "INSERT INTO T_LOCAL (ID) SELECT ID FROM CB_BILL@BILLING", encoding="utf-8")

    e = make_engine("sqlite://")
    store.create_all(e)
    code = CodeConfig(dao_sql_patterns=[DaoSqlPattern(path_contains=["/impl/"], conjunction="all")])
    build_lineage(tmp_path, code, TablesConfig(), e, now="2026-06-07T00:00:00",
                  dblink_index={"BILLING": "TEST_DB3"})

    edges = store.all_edges(e)
    # CB_BILL side (cross-db) should have db resolved to TEST_DB3
    cross = [x for x in edges if (x["src_table"] == "CB_BILL" and x["src_db"] == "TEST_DB3")
             or (x["dst_table"] == "CB_BILL" and x["dst_db"] == "TEST_DB3")]
    assert cross, (
        f"Expected CB_BILL cross-db edge with db=TEST_DB3, "
        f"got edges={[(x['src_table'], x['src_db'], x['dst_table'], x['dst_db']) for x in edges]}"
    )


def test_build_lineage_dblink_none_keeps_old_behavior(tmp_path):
    # dblink_index not passed -> old behavior: @dblink stripped, db not enriched
    sql_dir = tmp_path / "impl"
    sql_dir.mkdir()
    (sql_dir / "Foo.sql").write_text(
        "INSERT INTO T_LOCAL (ID) SELECT ID FROM CB_BILL@BILLING", encoding="utf-8")
    e = make_engine("sqlite://")
    store.create_all(e)
    code = CodeConfig(dao_sql_patterns=[DaoSqlPattern(path_contains=["/impl/"], conjunction="all")])
    build_lineage(tmp_path, code, TablesConfig(), e, now="2026-06-07T00:00:00")
    edges = store.all_edges(e)
    assert edges, "Expected at least one edge, got empty -- possible parsing failure"
    assert all(x["src_db"] == "" and x["dst_db"] == "" for x in edges), (
        f"Without dblink_index, expected all db fields empty, "
        f"got={[(x['src_table'], x['src_db'], x['dst_table'], x['dst_db']) for x in edges]}"
    )
