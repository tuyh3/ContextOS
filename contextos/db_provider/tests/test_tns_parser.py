"""tests for tns_parser: tnsnames.ora parsing + HOST descriptor resolution.

Design:
- parse_tnsnames_text parses TNS alias -> {host, port, service_name/sid} dict
- normalized_descriptor produces a (host, port, service_or_sid) comparison tuple
- resolve_host_descriptor handles three forms: TNS alias, inline DESCRIPTION, EZConnect
- resolve_host_descriptor returns None for unparseable input (fail-safe)
"""
from contextos.db_provider import tns_parser


_TNS = """
TEST_DB1 =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = db1.corp)(PORT = 1521))
    (CONNECT_DATA = (SERVICE_NAME = ctest1svc)))

TEST_DB3 =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = db2.corp)(PORT = 1522))
    (CONNECT_DATA = (SID = vcdev)))
"""


def test_parse_tnsnames_text():
    entries = tns_parser.parse_tnsnames_text(_TNS)
    assert entries["TEST_DB1"]["host"] == "db1.corp"
    assert entries["TEST_DB1"]["service_name"] == "ctest1svc"
    assert entries["TEST_DB3"]["sid"] == "vcdev"


def test_normalized_descriptor():
    entries = tns_parser.parse_tnsnames_text(_TNS)
    assert tns_parser.normalized_descriptor(entries["TEST_DB1"]) == ("db1.corp", 1521, "ctest1svc")


def test_resolve_host_alias():
    entries = tns_parser.parse_tnsnames_text(_TNS)
    # dblink HOST is a TNS alias
    assert tns_parser.resolve_host_descriptor("TEST_DB3", entries) == ("db2.corp", 1522, "vcdev")


def test_resolve_host_inline_descriptor():
    entries = {}
    host = "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db3.corp)(PORT=1521))(CONNECT_DATA=(SERVICE_NAME=svc3)))"
    assert tns_parser.resolve_host_descriptor(host, entries) == ("db3.corp", 1521, "svc3")


def test_resolve_host_ezconnect():
    assert tns_parser.resolve_host_descriptor("db4.corp:1530/svc4", {}) == ("db4.corp", 1530, "svc4")


def test_resolve_host_unparseable_returns_none():
    assert tns_parser.resolve_host_descriptor("garbage host", {}) is None
