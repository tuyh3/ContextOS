# contextos/config_dim/tests/test_schema.py
from sqlalchemy import create_engine, inspect
from contextos.config_dim.schema import metadata, ALL_TABLES


def test_all_tables_create_in_memory():
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    names = set(inspect(eng).get_table_names())
    expected = {
        "config_sources", "config_entities", "config_items", "config_snapshots",
        "config_bindings", "rule_sets", "rule_clauses", "rule_bindings",
        "config_changes", "config_evidence", "owner_resolution", "config_confirmation",
    }
    assert expected <= names


def test_owner_resolution_scoped_pk():
    # HIGH 1(R3): owner 解析按 (edge_id, module, datasource_key) scoped, 不是 edge 级
    pk = {c.name for c in metadata.tables["owner_resolution"].primary_key.columns}
    assert pk == {"edge_id", "module", "datasource_key"}
    cols = set(metadata.tables["owner_resolution"].columns.keys())
    assert {"resolved_src_db", "resolved_src_owner", "resolved_dst_db", "resolved_dst_owner"} <= cols


def test_config_confirmation_stable_key():
    # HIGH 2: 稳定身份 (customer_id, ref_type, ref_key)
    pk = {c.name for c in metadata.tables["config_confirmation"].primary_key.columns}
    assert pk == {"customer_id", "ref_type", "ref_key"}
