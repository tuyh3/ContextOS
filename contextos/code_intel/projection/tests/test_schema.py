"""schema: 9 表全建 / source_file 锚列每表都有 / name_lower 三表都有 / 索引存在 /
版本不符触发 drop 重建(数据清空)。"""
from __future__ import annotations

from sqlalchemy import inspect, insert, select

from contextos.code_intel.projection import schema as S


def test_all_tables_created(engine):
    S.create_all(engine)
    names = set(inspect(engine).get_table_names())
    assert {"code_files", "code_classes", "code_methods", "code_fields", "code_calls",
            "code_references", "code_inheritance", "code_table_refs",
            "code_projection_meta"} <= names


def test_source_file_anchor_on_every_data_table(engine):
    for t in (S.code_classes, S.code_methods, S.code_fields, S.code_calls,
              S.code_references, S.code_inheritance, S.code_table_refs):
        assert "source_file" in t.c, t.name
        assert "lang" in t.c, t.name


def test_name_lower_on_symbol_tables(engine):
    assert "name_lower" in S.code_classes.c
    assert "name_lower" in S.code_methods.c
    assert "name_lower" in S.code_fields.c


def test_duplicate_class_fqn_rows_coexist(engine):
    """F3 回归: 跨模块复制粘贴的同 FQN 工具类在真实大仓里存在 ——
    class_fqn 唯一性不是事实约束, 索引必须非 unique, 否则全量 build 单事务整体炸。"""
    S.ensure_projection_schema(engine)
    with engine.begin() as conn:
        conn.execute(insert(S.code_classes), [
            {"class_id": "C1", "class_fqn": "com.acme.util.StringHelper",
             "class_name": "StringHelper", "name_lower": "stringhelper",
             "source_file": "module-a/src/StringHelper.java"},
            {"class_id": "C2", "class_fqn": "com.acme.util.StringHelper",
             "class_name": "StringHelper", "name_lower": "stringhelper",
             "source_file": "module-b/src/StringHelper.java"},
        ])
    with engine.connect() as conn:
        rows = conn.execute(select(S.code_classes.c.source_file)).fetchall()
    assert len(rows) == 2


def test_version_mismatch_drops_and_recreates(engine):
    S.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(S.code_classes), [{
            "class_id": "x1", "class_fqn": "com.acme.A", "class_name": "A",
            "name_lower": "a", "source_file": "src/A.java"}])
        conn.execute(insert(S.code_projection_meta),
                     [{"key": "schema_version", "value": "0-stale"}])
    S.ensure_projection_schema(engine)  # 版本不符 -> drop + 重建
    with engine.connect() as conn:
        rows = conn.execute(select(S.code_classes)).fetchall()
        ver = conn.execute(select(S.code_projection_meta.c.value).where(
            S.code_projection_meta.c.key == "schema_version")).scalar_one()
    assert rows == []
    assert ver == S.PROJECTION_SCHEMA_VERSION


def test_version_match_keeps_data(engine):
    S.ensure_projection_schema(engine)
    with engine.begin() as conn:
        conn.execute(insert(S.code_classes), [{
            "class_id": "x1", "class_fqn": "com.acme.A", "class_name": "A",
            "name_lower": "a", "source_file": "src/A.java"}])
    S.ensure_projection_schema(engine)  # 幂等
    with engine.connect() as conn:
        assert len(conn.execute(select(S.code_classes)).fetchall()) == 1
