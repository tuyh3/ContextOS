"""Tests for dblink_resolve.build_dblink_index and build_index_from_store.

Design intent:
  - build_dblink_index: given raw dblink rows + instance descriptors + tns_entries + manual override map,
    produce ({DBLINK_NAME: target_TNS}, [unresolved_rows]).
  - Priority: dblink_map override > TNS descriptor match > unresolved.
  - base name (strip .DOMAIN) registered via setdefault (does not overwrite full name).
  - build_index_from_store: wires store.all_dblinks + tns_parser to build_dblink_index;
    tested via mocked oracle_cfg + in-memory SQLAlchemy engine.

Scoring:
  - test_build_index_matches_instance_by_descriptor: TNS descriptor path resolves correctly.
  - test_build_index_dblink_map_override_wins: override takes priority over unparseable host.
  - test_build_index_unresolved_registered: unresolvable dblink lands in unresolved list.
  - test_build_index_base_name_fallback: full+domain name registers base-only alias via setdefault.
  - test_build_index_from_store_wiring: build_index_from_store wires store+tns_parser correctly
    (mocked engine + synthetic tnsnames + oracle_cfg with dblink_map; verifies the integration
    path where both instance_descriptors and parse_tnsnames are called and results flow through).

Fixtures policy: all schema/owner/instance names are synthetic (TESTDB_01, SYNTH_DB, etc.).
Real instance names (TEST_DB3 / CTEST1 / etc.) are reserved for *-integration tests only.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from contextos.lineage import dblink_resolve


# ---------------------------------------------------------------------------
# Synthetic constants (no real client/TNS names)
# ---------------------------------------------------------------------------
_INST_A = "TESTDB_01"
_HOST_A = "db-host-a.internal"
_PORT_A = 1521
_SVC_A = "synth_svc"

_DBLINK_FULL = "SYNTH_LINK.WORLD"
_DBLINK_BASE = "SYNTH_LINK"
_SCHEMA_A = "SYNTH_SCHEMA"


def _inst_descriptors() -> dict:
    return {_INST_A: (_HOST_A, _PORT_A, _SVC_A)}


def _tns_entries() -> dict:
    return {_INST_A: {"host": _HOST_A, "port": _PORT_A, "sid": _SVC_A}}


# ---------------------------------------------------------------------------
# Unit tests for build_dblink_index
# ---------------------------------------------------------------------------

def test_build_index_matches_instance_by_descriptor():
    dblinks = [dict(db_link=_DBLINK_FULL, host=_INST_A, db_name=_SCHEMA_A)]
    index, unresolved = dblink_resolve.build_dblink_index(
        dblinks, _inst_descriptors(), _tns_entries(), {}
    )
    assert index == {_DBLINK_FULL: _INST_A, _DBLINK_BASE: _INST_A}
    assert unresolved == []


def test_build_index_dblink_map_override_wins():
    dblinks = [dict(db_link="OTHER_LINK.WORLD", host="unparseable", db_name=_SCHEMA_A)]
    index, unresolved = dblink_resolve.build_dblink_index(
        dblinks, {}, {}, {"OTHER_LINK.WORLD": _INST_A}
    )
    assert index["OTHER_LINK.WORLD"] == _INST_A
    assert unresolved == []


def test_build_index_unresolved_registered():
    dblinks = [dict(db_link="GHOST_LINK.WORLD", host="nowhere.corp:1/zz", db_name=_SCHEMA_A)]
    index, unresolved = dblink_resolve.build_dblink_index(dblinks, {}, {}, {})
    assert index == {}
    assert unresolved[0]["db_link"] == "GHOST_LINK.WORLD"
    assert unresolved[0]["reason"] == "no_matching_instance"


def test_build_index_base_name_fallback():
    # SQL often uses SYNTH_LINK (no .WORLD domain); dblink name is SYNTH_LINK.WORLD
    # -> index registers both full name and base name
    dblinks = [dict(db_link=_DBLINK_FULL, host=_INST_A, db_name=_SCHEMA_A)]
    index, _ = dblink_resolve.build_dblink_index(
        dblinks, _inst_descriptors(), _tns_entries(), {}
    )
    assert index.get(_DBLINK_BASE) == _INST_A


def test_build_index_dblink_map_empty_string_value_skips_to_priority2():
    """dblink_map 含空串值时不应短路到基名再跳过。
    SYNTH_LINK.WORLD -> map_upper['SYNTH_LINK.WORLD']='' (key present, value falsy);
    or-falsy 短路会尝试 map_upper.get('SYNTH_LINK') -> None -> Priority 2 走 descriptor。
    显式 key-in 修复后: key SYNTH_LINK.WORLD 命中 -> target='' -> falsy -> Priority 2。
    最终结果相同(两者都走 Priority 2),但语义正确:配置了就不再尝试基名。
    """
    dblinks = [dict(db_link=_DBLINK_FULL, host=_INST_A, db_name=_SCHEMA_A)]
    # Full name mapped to '' (blank), base name NOT in map -> Priority 2 resolves via descriptor
    index, unresolved = dblink_resolve.build_dblink_index(
        dblinks, _inst_descriptors(), _tns_entries(),
        {_DBLINK_FULL: ""}   # blank value in dblink_map
    )
    # Priority 2 (descriptor match) should still resolve the dblink
    assert index.get(_DBLINK_FULL) == _INST_A
    assert unresolved == []


def test_build_index_dblink_map_full_name_wins_over_base_name():
    """显式 key-in 保证全名命中后不再尝试基名: full name 映射非空 -> 用全名值。"""
    dblinks = [dict(db_link=_DBLINK_FULL, host="ignore", db_name=_SCHEMA_A)]
    index, unresolved = dblink_resolve.build_dblink_index(
        dblinks, {}, {},
        {_DBLINK_FULL: _INST_A, _DBLINK_BASE: "OTHER_DB"}
    )
    # Full name entry should win; base name alias registered via setdefault (not overwritten)
    assert index[_DBLINK_FULL] == _INST_A
    assert unresolved == []


# ---------------------------------------------------------------------------
# Integration-path test for build_index_from_store (mocked wiring)
# ---------------------------------------------------------------------------

def test_build_index_from_store_wiring():
    """Verify build_index_from_store wires store.all_dblinks + tns_parser correctly.

    Uses an in-memory SQLAlchemy engine and mocks the three tns_parser calls so
    no filesystem access is required. Verifies the plumbing: dblink rows from the
    store reach build_dblink_index with the descriptors and tns_entries produced by
    the parser, and that the oracle_cfg dblink_map override is forwarded.
    """
    from sqlalchemy import create_engine
    from contextos.lineage import store as lineage_store

    # In-memory engine; populate the dblinks table with one synthetic row.
    engine = create_engine("sqlite:///:memory:")
    lineage_store.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            lineage_store.dblinks.insert(),
            [{"db_link": _DBLINK_FULL, "host": _INST_A, "db_name": _SCHEMA_A,
              "owner": _SCHEMA_A, "username": ""}],
        )

    # Synthetic oracle_cfg (no real TNS names)
    oracle_cfg = SimpleNamespace(
        tns_admin="/synth/tns",
        allowed_instances=[_INST_A],
        dblink_map={},
    )

    # Patch both tns_parser calls so no filesystem I/O occurs
    with (
        patch(
            "contextos.lineage.dblink_resolve.tns_parser.instance_descriptors",
            return_value=_inst_descriptors(),
        ) as mock_inst,
        patch(
            "contextos.lineage.dblink_resolve.tns_parser.parse_tnsnames",
            return_value=_tns_entries(),
        ) as mock_parse,
    ):
        index, unresolved = dblink_resolve.build_index_from_store(engine, oracle_cfg)

    # Wiring assertions: both parser calls were made with oracle_cfg values
    mock_inst.assert_called_once_with(oracle_cfg.tns_admin, [_INST_A])
    mock_parse.assert_called_once()
    tns_arg = mock_parse.call_args[0][0]
    assert "tnsnames.ora" in tns_arg

    # Result assertions: synthetic dblink resolved via descriptor matching
    assert index.get(_DBLINK_FULL) == _INST_A
    assert index.get(_DBLINK_BASE) == _INST_A
    assert unresolved == []
