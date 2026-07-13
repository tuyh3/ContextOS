"""Test SQLcl wrapper: whitelist enforcement + read-only safety."""
import pytest


def _minimal_profile(allowed: list[str]):
    """Construct a minimal Profile for db_provider unit tests (neutral fixture, no real TNS names in non-integration context)."""
    from contextos.profile.schema import Profile
    return Profile(**{
        "llm": {"provider": "fake", "api_key_env": "FAKE_KEY"},
        "embedding": {"model": "BAAI/bge-m3"},
        "reranker": {"enabled": True, "model": "fake", "top_k_input": 50, "top_k_output": 10},
        "query_expansion": {"enabled": True,
                            "translation_provider": "main_llm",
                            "fallback_provider": "local_qwen"},
        "storage": {"data_dir": "/tmp/x"},
        "ingestion": {"default_cleanup": "full",
                      "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "/jdtls",
                          "lombok_path": "/jdtls/l.jar", "java_home": "/jre"},
        "oracle": {"tns_admin": "/tns", "allowed_instances": allowed},
        "projects": [{"name": "proj", "path": "/proj",
                      "language": "java", "build_system": "gradle"}],
    })


def test_whitelist_rejects_unknown_instance():
    from contextos.db_provider.sqlcl_mcp import OracleClient, OracleConfig, OracleSafetyError
    cfg = OracleConfig(
        tns_admin="/tmp",
        allowed_instances=["TEST_DB1"],
    )
    with pytest.raises(OracleSafetyError, match="not in allowed_instances"):
        OracleClient(tns_name="SOMEUNKNOWN_DB", config=cfg, user="x", password="x")


def test_whitelist_rejects_production_keyword_even_if_in_list():
    from contextos.db_provider.sqlcl_mcp import OracleClient, OracleConfig, OracleSafetyError
    cfg = OracleConfig(
        tns_admin="/tmp",
        allowed_instances=["TEST_DB1", "PROD_DEMO_DB"],
    )
    with pytest.raises(OracleSafetyError, match="(?i)production"):
        OracleClient(tns_name="PROD_DEMO_DB", config=cfg, user="x", password="x")


def test_readonly_check_rejects_dml():
    # Migrated from private _assert_readonly to public assert_query_is_readonly
    # (oracle_gate.py extracted in Task 7). Note: oracle_gate is more conservative
    # than old _assert_readonly — it does NOT strip string literals before keyword
    # scanning, so SELECT 'drop ...' FROM dual is intentionally rejected.
    from contextos.db_provider.oracle_gate import assert_query_is_readonly, OracleSafetyError
    # positive
    assert_query_is_readonly("select * from dual")
    assert_query_is_readonly("SELECT * FROM ALL_TAB_COMMENTS")
    assert_query_is_readonly("WITH t AS (select 1 from dual) select * from t")
    assert_query_is_readonly("SELECT * FROM tab -- this drop comment is fine\nWHERE 1=1")
    # negative single-statement DML/DDL
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("update foo set x=1")
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("delete from foo")
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("drop table foo")
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("truncate table foo")
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("alter table foo add x int")
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("merge into foo using bar on ...")
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("grant select on foo to public")
    # PL/SQL
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("begin update foo set x=1; end;")
    # multi-statement bypass
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("select * from dual; drop table foo")
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("select 1 from dual; alter table foo add x int;")
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("select * from dual;\n  delete from foo where 1=1")
    with pytest.raises(OracleSafetyError): assert_query_is_readonly("select 1 from dual; update foo set x=2; select 3 from dual")


def test_connect_from_profile_rejects_non_profile():
    from contextos.db_provider.sqlcl_mcp import connect_from_profile
    with pytest.raises(TypeError, match="expected Profile"):
        connect_from_profile("not a profile")


def test_connect_from_profile_falls_back_to_first_instance(monkeypatch):
    """无 tns 时连 allowed_instances[0](多库无主库, 单库回落首个实例)。"""
    from contextos.db_provider.sqlcl_mcp import connect_from_profile
    monkeypatch.setenv("ORACLE_TEST_INST1_USER", "u")
    monkeypatch.setenv("ORACLE_TEST_INST1_PASSWORD", "p")
    prof = _minimal_profile(allowed=["TEST_INST1", "TEST_INST2"])
    client = connect_from_profile(prof)          # 不传 tns
    assert client._tns == "TEST_INST1"           # 回落 allowed_instances[0]


# ---------------------------------------------------------------------------
# charset 容错: 某电信客户 Oracle(AL32UTF8 但混入非 UTF-8 脏字节, 如 0xce)读元数据时
# UnicodeDecodeError 崩 -> output type handler 对文本列 encoding_errors='replace' 容错。
# ---------------------------------------------------------------------------


def test_tolerant_output_handler_wraps_text_types_with_replace():
    """文本列(VARCHAR/CHAR/LONG/NVARCHAR/NCHAR)-> cursor.var(encoding_errors='replace');
    非文本(NUMBER 等)-> None(走默认), 不干扰数值/日期解析。"""
    import oracledb
    from contextos.db_provider.sqlcl_mcp import _tolerant_output_handler

    captured = {}

    class FakeCur:
        arraysize = 100
        def var(self, typ, **kw):
            captured["typ"], captured["kw"] = typ, kw
            return ("VAR", typ, kw)

    class Meta:
        def __init__(self, tc): self.type_code = tc

    for tc in (oracledb.DB_TYPE_VARCHAR, oracledb.DB_TYPE_CHAR, oracledb.DB_TYPE_LONG,
               oracledb.DB_TYPE_NVARCHAR, oracledb.DB_TYPE_NCHAR):
        captured.clear()
        out = _tolerant_output_handler(FakeCur(), Meta(tc))
        assert out is not None, f"{tc} 应被容错包装"
        assert captured["kw"].get("encoding_errors") == "replace"

    assert _tolerant_output_handler(FakeCur(), Meta(oracledb.DB_TYPE_NUMBER)) is None


def test_connect_installs_tolerant_output_handler(monkeypatch):
    """OracleClient.__enter__ 连接后把容错 handler 挂到 connection.outputtypehandler。"""
    import contextos.db_provider.sqlcl_mcp as m

    class FakeConn:
        def __init__(self): self.outputtypehandler = None
        def close(self): pass

    fake = FakeConn()
    monkeypatch.setattr(m.oracledb, "connect", lambda **kw: fake)
    cfg = m.OracleConfig(tns_admin="/tns", allowed_instances=["TEST_X"])
    client = m.OracleClient(tns_name="TEST_X", config=cfg, user="u", password="p")
    with client:
        assert fake.outputtypehandler is m._tolerant_output_handler


# ---------------------------------------------------------------------------
# 短连接超时: oracle offline 时, 默认 oracledb 连接超时是几十秒级, fan_out 逐个
# 等满 (×N 白名单实例) 让 health_check 卡几分钟。__enter__ 必须把 connect_timeout_seconds
# 作为 tcp_connect_timeout 传给 oracledb.connect, 使死库快速放弃 (秒级)。
# ---------------------------------------------------------------------------


def test_connect_passes_short_tcp_connect_timeout(monkeypatch):
    """__enter__ 把 config.connect_timeout_seconds 作为 tcp_connect_timeout 传给 oracledb.connect。"""
    import contextos.db_provider.sqlcl_mcp as m

    captured: dict = {}

    class FakeConn:
        def __init__(self): self.outputtypehandler = None
        def close(self): pass

    def fake_connect(**kw):
        captured.update(kw)
        return FakeConn()

    monkeypatch.setattr(m.oracledb, "connect", fake_connect)
    cfg = m.OracleConfig(tns_admin="/tns", allowed_instances=["TEST_X"],
                         connect_timeout_seconds=3)
    client = m.OracleClient(tns_name="TEST_X", config=cfg, user="u", password="p")
    with client:
        pass
    assert captured.get("tcp_connect_timeout") == 3


def test_oracle_config_connect_timeout_defaults_to_5(monkeypatch):
    """dataclass 默认 connect_timeout_seconds=5; profile schema 默认=5 并串到 OracleClient。"""
    import contextos.db_provider.sqlcl_mcp as m
    # dataclass 默认
    assert m.OracleConfig(tns_admin="/t", allowed_instances=["TEST_X"]).connect_timeout_seconds == 5
    # profile schema 默认 + 串到 client
    monkeypatch.setenv("ORACLE_TEST_INST1_USER", "u")
    monkeypatch.setenv("ORACLE_TEST_INST1_PASSWORD", "p")
    prof = _minimal_profile(allowed=["TEST_INST1"])
    assert prof.database.oracle.connect_timeout_seconds == 5
    client = m.connect_from_profile(prof)
    assert client._connect_timeout == 5
