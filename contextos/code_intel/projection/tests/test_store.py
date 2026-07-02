"""store: replace_all 原子换 / delete_rows_for_files 只删目标文件行 /
meta upsert / 计数。"""
from __future__ import annotations

from sqlalchemy import select

from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection import store


def _row_cls(fqn: str, sf: str) -> dict:
    name = fqn.rsplit(".", 1)[-1]
    return {"class_id": fqn, "class_fqn": fqn, "class_name": name,
            "name_lower": name.lower(), "source_file": sf}


def test_replace_all_swaps_atomically(engine):
    S.ensure_projection_schema(engine)
    store.replace_all(engine, {"code_classes": [_row_cls("com.acme.A", "src/A.java")]})
    store.replace_all(engine, {"code_classes": [_row_cls("com.acme.B", "src/B.java")]})
    with engine.connect() as conn:
        fqns = [r[0] for r in conn.execute(select(S.code_classes.c.class_fqn))]
    assert fqns == ["com.acme.B"]  # 全量替换语义


def test_delete_rows_for_files(engine):
    S.ensure_projection_schema(engine)
    store.replace_all(engine, {"code_classes": [
        _row_cls("com.acme.A", "src/A.java"), _row_cls("com.acme.B", "src/B.java")],
        "code_files": [{"file_path": "src/A.java", "sha1": "a" * 40},
                       {"file_path": "src/B.java", "sha1": "b" * 40}]})
    with engine.begin() as conn:
        store.delete_rows_for_files_conn(conn, ["src/A.java"])
    with engine.connect() as conn:
        fqns = [r[0] for r in conn.execute(select(S.code_classes.c.class_fqn))]
        files = [r[0] for r in conn.execute(select(S.code_files.c.file_path))]
    assert fqns == ["com.acme.B"]
    assert files == ["src/B.java"]


def test_meta_upsert_and_get(engine):
    S.ensure_projection_schema(engine)
    store.set_meta(engine, "last_indexed_commit", "abc123")
    store.set_meta(engine, "last_indexed_commit", "def456")
    assert store.get_meta(engine, "last_indexed_commit") == "def456"
    assert store.get_meta(engine, "nope") is None


def test_table_counts(engine):
    S.ensure_projection_schema(engine)
    store.replace_all(engine, {"code_classes": [_row_cls("com.acme.A", "src/A.java")]})
    counts = store.table_counts(engine)
    assert counts["code_classes"] == 1
    assert counts["code_table_refs"] == 0


def test_jar_run_scoped_ids_do_not_collide(engine):
    """F1 回归: jar 的 class_id/method_id/field_id/call_id 每次运行从 C1/M1/F1/X1
    重新计数(run-scoped)。增量子集第二次跑出的 C1 必须能与库内未触碰文件的 C1
    共存 —— 这四个 id 只是溯源列, 唯一性由代理主键 row_id 保证。"""
    S.ensure_projection_schema(engine)
    store.replace_all(engine, {
        "code_classes": [{"class_id": "C1", "class_fqn": "com.acme.A", "class_name": "A",
                          "name_lower": "a", "source_file": "src/A.java"}],
        "code_methods": [{"method_id": "M1", "class_fqn": "com.acme.A",
                          "method_name": "run", "name_lower": "run",
                          "source_file": "src/A.java"}],
        "code_fields": [{"field_id": "F1", "class_fqn": "com.acme.A",
                         "field_name": "x", "name_lower": "x",
                         "source_file": "src/A.java"}],
        "code_calls": [{"call_id": "X1", "caller_method_fqn": "com.acme.A.run()",
                        "source_file": "src/A.java"}],
    })
    # 模拟增量子集 jar 重新计数: 同名 id 不同实体, 插入不许撞 PK
    with engine.begin() as conn:
        store.insert_rows_conn(conn, {
            "code_classes": [{"class_id": "C1", "class_fqn": "com.acme.B",
                              "class_name": "B", "name_lower": "b",
                              "source_file": "src/B.java"}],
            "code_methods": [{"method_id": "M1", "class_fqn": "com.acme.B",
                              "method_name": "go", "name_lower": "go",
                              "source_file": "src/B.java"}],
            "code_fields": [{"field_id": "F1", "class_fqn": "com.acme.B",
                             "field_name": "y", "name_lower": "y",
                             "source_file": "src/B.java"}],
            "code_calls": [{"call_id": "X1", "caller_method_fqn": "com.acme.B.go()",
                            "source_file": "src/B.java"}],
        })
    counts = store.table_counts(engine)
    assert counts["code_classes"] == 2
    assert counts["code_methods"] == 2
    assert counts["code_fields"] == 2
    assert counts["code_calls"] == 2


def test_replace_all_conn_rolls_back_with_outer_txn(engine):
    """连接级变体在外层事务回滚时整体撤销(build 单事务 staging 的地基)。"""
    S.ensure_projection_schema(engine)
    store.replace_all(engine, {"code_classes": [_row_cls("com.acme.Old", "src/O.java")]})

    class _Boom(Exception):
        pass

    try:
        with engine.begin() as conn:
            store.replace_all_conn(conn, {"code_classes": [_row_cls("com.acme.New", "src/N.java")]})
            raise _Boom()
    except _Boom:
        pass
    with engine.connect() as conn:
        fqns = [r[0] for r in conn.execute(select(S.code_classes.c.class_fqn))]
    assert fqns == ["com.acme.Old"]   # 回滚后旧行原样
