"""Tests for discover_owners (Block 2 Task 3).

Design:
- discover_owners queries ALL_OBJECTS for distinct owners, then filters via
  fnmatch glob patterns from exclude_schemas.
- CRITICAL failure semantics: query failure must raise, NOT return [].
  An empty-owners result fed to refresh_*_multi would WIPE the snapshot.
  This is the source-side guard; Task 2's empty-owners guard is the backstop.

Scoring:
- test_discover_owners_filters_system_and_stage_glob: verifies glob patterns
  (e.g., "*_STAGE", "*_STAGING") work alongside exact names.
- test_discover_owners_exact_names_backward_compat: verifies exact-name excludes
  still work (no regression).
- test_discover_owners_failure_raises_not_empty: HIGH1 data-loss guard -- a dead
  querier must propagate its exception upward, not silently return [].

Stub behaviour:
- _OwnersQuerier asserts the SQL targets ALL_OBJECTS with DISTINCT (both survive
  _fetch_all_meta's ROWNUM wrap).
- _DeadQuerier simulates ORA-00942 / insufficient privilege.
"""
import pytest

from contextos.lineage.oracle_metadata import discover_owners


class _OwnersQuerier:
    def __init__(self, owners):
        self._owners = owners

    def query(self, sql, params=None):
        assert "ALL_OBJECTS" in sql and "DISTINCT" in sql.upper()
        return [{"OWNER": o} for o in self._owners]


class _DeadQuerier:
    def query(self, sql, params=None):
        raise RuntimeError("ORA-00942 / insufficient privilege")


def test_discover_owners_filters_system_and_stage_glob():
    q = _OwnersQuerier(["SYS", "SYSTEM", "UPC", "SEC", "CB_STAGE", "OM_STAGING"])
    out = discover_owners(q, ["SYS", "SYSTEM", "XDB", "MDSYS", "*_STAGE", "*_STAGING"])
    assert out == ["SEC", "UPC"]          # system + stage glob filtered, sorted


def test_discover_owners_exact_names_backward_compat():
    q = _OwnersQuerier(["SYS", "UPC"])
    assert discover_owners(q, ["SYS", "SYSTEM", "XDB", "MDSYS"]) == ["UPC"]


def test_discover_owners_failure_raises_not_empty():
    # HIGH1: query failure must raise, never silently return []
    # (empty owners fed to refresh_*_multi would WIPE the snapshot)
    with pytest.raises(Exception):
        discover_owners(_DeadQuerier(), ["SYS"])
