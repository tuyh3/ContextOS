"""Tests for OfflineSchema regex CREATE TABLE parser.

Task 10 imports OfflineSchema for offline candidate lookup, so the parser's
edge-case behavior needs regression coverage. POC version is regex-based; v1
will move to SQLGlot — these tests will guide that migration.
"""
from pathlib import Path

from contextos.db_provider.offline_ddl import OfflineSchema


def _write_sql(dir_: Path, name: str, content: str) -> Path:
    p = dir_ / name
    p.write_text(content, encoding="utf-8")
    return p


def test_parse_simple_create_table(tmp_path: Path) -> None:
    """Baseline: a vanilla CREATE TABLE with 3 columns is parsed correctly."""
    _write_sql(tmp_path, "simple.sql", """
        CREATE TABLE CB_CUSTOMER (
            CUST_ID NUMBER(18),
            CUST_NAME VARCHAR2(64),
            CREATED_AT DATE
        );
    """)
    schema = OfflineSchema.from_directory(tmp_path)
    assert schema.list_tables() == ["CB_CUSTOMER"]
    tbl = schema.find_table("CB_CUSTOMER")
    assert tbl is not None
    col_names = [c.name for c in tbl.columns]
    assert col_names == ["CUST_ID", "CUST_NAME", "CREATED_AT"]
    # find_table is case-insensitive
    assert schema.find_table("cb_customer") is tbl


def test_parse_quoted_identifier(tmp_path: Path) -> None:
    """Table and column names wrapped in double-quotes still match."""
    _write_sql(tmp_path, "quoted.sql", """
        CREATE TABLE "CHNL_DEALER" (
            "DEALER_ID" NUMBER(10),
            "DEALER_NAME" VARCHAR2(80)
        );
    """)
    schema = OfflineSchema.from_directory(tmp_path)
    tbl = schema.find_table("CHNL_DEALER")
    assert tbl is not None
    assert [c.name for c in tbl.columns] == ["DEALER_ID", "DEALER_NAME"]


def test_parse_missing_terminator_falls_back_to_eof(tmp_path: Path) -> None:
    """No `);` terminator — parser should still parse columns to end of file
    rather than skipping the table entirely (defensive behavior so a malformed
    DDL dump doesn't lose tables silently)."""
    _write_sql(tmp_path, "no_terminator.sql", """
        CREATE TABLE ORD_HEADER (
            ORDER_ID NUMBER(18),
            STATUS_CODE VARCHAR2(8),
            CREATED_AT DATE
    """)  # NB: no `);`
    schema = OfflineSchema.from_directory(tmp_path)
    tbl = schema.find_table("ORD_HEADER")
    assert tbl is not None
    # All 3 cols still picked up before EOF
    assert [c.name for c in tbl.columns] == ["ORDER_ID", "STATUS_CODE", "CREATED_AT"]


def test_find_tables_by_keyword_matches_table_or_column(tmp_path: Path) -> None:
    """Keyword lookup hits substrings of either table name OR any column name,
    case-insensitively. Task 10 relies on this for offline candidate scoring."""
    _write_sql(tmp_path, "a.sql", """
        CREATE TABLE FREE_RES_MID (
            RES_TYPE VARCHAR2(8),
            BALANCE NUMBER(18)
        );
    """)
    _write_sql(tmp_path, "b.sql", """
        CREATE TABLE VOICE_R (
            CALLER_NO VARCHAR2(32),
            FREE_FLAG CHAR(1)
        );
    """)
    _write_sql(tmp_path, "c.sql", """
        CREATE TABLE CUSTOMER (
            CUST_ID NUMBER(18),
            CUST_NAME VARCHAR2(64)
        );
    """)
    schema = OfflineSchema.from_directory(tmp_path)
    assert sorted(schema.list_tables()) == ["CUSTOMER", "FREE_RES_MID", "VOICE_R"]

    # 'FREE' hits table FREE_RES_MID (table-name substring) AND VOICE_R (FREE_FLAG col substring)
    free_hits = {t.name for t in schema.find_tables_by_keyword("FREE")}
    assert free_hits == {"FREE_RES_MID", "VOICE_R"}

    # Case-insensitive
    assert {t.name for t in schema.find_tables_by_keyword("free")} == free_hits

    # Column-only match: 'CUST_NAME' substring only via column on CUSTOMER
    assert {t.name for t in schema.find_tables_by_keyword("cust_name")} == {"CUSTOMER"}

    # No-match returns empty list
    assert schema.find_tables_by_keyword("NONEXISTENT_TOKEN") == []


def test_empty_directory_returns_empty_schema(tmp_path: Path) -> None:
    """No SQL files → empty schema, not a crash."""
    schema = OfflineSchema.from_directory(tmp_path)
    assert schema.list_tables() == []
    assert schema.find_table("ANY") is None
    assert schema.find_tables_by_keyword("ANY") == []


def test_nonexistent_directory_returns_empty_schema(tmp_path: Path) -> None:
    """Directory doesn't exist → empty schema (defensive: lets Task 10 call
    OfflineSchema.from_directory unconditionally without an exists() guard)."""
    schema = OfflineSchema.from_directory(tmp_path / "does_not_exist")
    assert schema.list_tables() == []


def test_parse_schema_qualified_create_table(tmp_path: Path) -> None:
    """A large real customer DDL dump uses `CREATE TABLE SCHEMA.NAME (...)` ~93% of the time.
    The old regex matched bare names only and silently dropped 96% of the
    dump; this test pins the schema-prefix path."""
    _write_sql(tmp_path, "qualified.sql", """
        CREATE TABLE RES.RES_PAYCARD_STORY_HIS (
            CARD_ID    VARCHAR2(20),
            USER_ID    NUMBER(18),
            CREATED_AT DATE
        );
    """)
    schema = OfflineSchema.from_directory(tmp_path)
    assert schema.list_tables() == ["RES.RES_PAYCARD_STORY_HIS"]
    tbl = schema.find_table("RES.RES_PAYCARD_STORY_HIS")
    assert tbl is not None
    assert tbl.schema == "RES"
    assert tbl.name == "RES_PAYCARD_STORY_HIS"
    assert [c.name for c in tbl.columns] == ["CARD_ID", "USER_ID", "CREATED_AT"]


def test_find_table_supports_bare_and_qualified_lookup(tmp_path: Path) -> None:
    """find_table accepts both SCHEMA.NAME and bare NAME — the cache is keyed
    on the qualified form but Task 10 may have a bare token from gold."""
    _write_sql(tmp_path, "q.sql", """
        CREATE TABLE QD.RSO_SELF_ACC_BOOK (
            BOOK_ID   NUMBER(18),
            ACC_TYPE  VARCHAR2(8)
        );
    """)
    schema = OfflineSchema.from_directory(tmp_path)
    # Qualified lookup hits the direct key
    qualified = schema.find_table("QD.RSO_SELF_ACC_BOOK")
    # Bare lookup walks the suffix-match fallback
    bare = schema.find_table("RSO_SELF_ACC_BOOK")
    # Case-insensitive on the bare form too
    lower = schema.find_table("rso_self_acc_book")
    assert qualified is not None
    assert qualified is bare
    assert qualified is lower
    assert qualified.schema == "QD"
    # find_table on something that doesn't exist returns None
    assert schema.find_table("NO_SUCH_TABLE") is None


def test_parse_multiple_create_tables_in_one_file(tmp_path: Path) -> None:
    """Some real customer files bundle multiple CREATE TABLE statements (QD.addTable.sql
    ships 15). The parser used to call `.search()` and silently drop all but
    the first; finditer fixes this."""
    _write_sql(tmp_path, "multi.sql", """
        CREATE TABLE NEA.CFG_DYNC_TABLE_SPLIT (
            TBL_NAME  VARCHAR2(64),
            SPLIT_RULE VARCHAR2(128)
        );

        CREATE TABLE NEA.CFG_ID_GENERATOR (
            GEN_ID  NUMBER(18),
            GEN_NAME VARCHAR2(64),
            CUR_VAL NUMBER(18)
        );

        CREATE TABLE NEA.CFG_METHOD_CENTER (
            METHOD_ID NUMBER(18),
            METHOD_NAME VARCHAR2(128)
        );
    """)
    schema = OfflineSchema.from_directory(tmp_path)
    assert sorted(schema.list_tables()) == [
        "NEA.CFG_DYNC_TABLE_SPLIT",
        "NEA.CFG_ID_GENERATOR",
        "NEA.CFG_METHOD_CENTER",
    ]
    # Verify each table got its OWN columns — body-bounding by next CREATE
    # TABLE position is what stops the middle table's columns from leaking
    # into the third one's body.
    assert [c.name for c in schema.find_table("NEA.CFG_DYNC_TABLE_SPLIT").columns] == [  # type: ignore[union-attr]
        "TBL_NAME", "SPLIT_RULE",
    ]
    assert [c.name for c in schema.find_table("NEA.CFG_ID_GENERATOR").columns] == [  # type: ignore[union-attr]
        "GEN_ID", "GEN_NAME", "CUR_VAL",
    ]
    assert [c.name for c in schema.find_table("NEA.CFG_METHOD_CENTER").columns] == [  # type: ignore[union-attr]
        "METHOD_ID", "METHOD_NAME",
    ]
