"""抽样对照(spec §3.1 条件 3): 投影抽 N 符号问 live JDT workspaceSymbol,
返回 mismatch 率。searcher 注入 fake; 吃 Connection(staging 事务内调用)。"""
from __future__ import annotations

from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection import store
from contextos.code_intel.projection.sample_check import sample_mismatch_ratio


def _seed(engine, n: int):
    S.ensure_projection_schema(engine)
    store.replace_all(engine, {"code_classes": [
        {"class_id": f"c{i}", "class_fqn": f"com.acme.K{i}", "class_name": f"K{i}",
         "name_lower": f"k{i}", "source_file": f"src/K{i}.java"} for i in range(n)]})


class _JdtAllFound:
    def request_workspace_symbol(self, query):
        return [{"name": query, "containerName": "com.acme"}]


class _JdtNothing:
    def request_workspace_symbol(self, query):
        return []


class _JdtCrashes:
    def request_workspace_symbol(self, query):
        raise RuntimeError("jdt died")


def test_zero_mismatch(engine):
    _seed(engine, 10)
    with engine.connect() as conn:
        assert sample_mismatch_ratio(conn, _JdtAllFound(), n_classes=5, n_methods=0) == 0.0


def test_full_mismatch(engine):
    _seed(engine, 10)
    with engine.connect() as conn:
        assert sample_mismatch_ratio(conn, _JdtNothing(), n_classes=5, n_methods=0) == 1.0


def test_jdt_crash_counts_as_miss(engine):
    _seed(engine, 10)
    with engine.connect() as conn:
        assert sample_mismatch_ratio(conn, _JdtCrashes(), n_classes=5, n_methods=0) == 1.0


def test_empty_projection_is_zero(engine):
    S.ensure_projection_schema(engine)
    with engine.connect() as conn:
        assert sample_mismatch_ratio(conn, _JdtNothing(), n_classes=5, n_methods=5) == 0.0


def test_reads_uncommitted_staging_rows(engine):
    """staging 语义: 在未 commit 的事务里抽样, 必须看得到本事务灌的行。"""
    S.ensure_projection_schema(engine)
    from sqlalchemy import insert
    with engine.begin() as conn:
        conn.execute(insert(S.code_classes), [{
            "class_id": "x", "class_fqn": "com.acme.Stage", "class_name": "Stage",
            "name_lower": "stage", "source_file": "src/S.java"}])
        ratio = sample_mismatch_ratio(conn, _JdtAllFound(), n_classes=5, n_methods=0)
        assert ratio == 0.0   # 抽到了 staging 行且 JDT 全找到
