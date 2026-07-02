"""W2: path B 批量(O(owners) 非 O(tables))。

设计思路(memory feedback_contextos_test_documentation):
- path B(Oracle DDL COMMENT)是 ALL_TAB_COMMENTS WHERE owner IN (...) 的单查, 一次可覆盖
  该 owner 全部表。C5 旧 build 在 per-table 四路循环里每表都调一次 path_b_query, 对同 owner
  的 N 张表查 N 次(实库 N 次 Oracle round-trip 浪费)。
- 本 task 验证: 同 owner 多表 -> path B 只查 1 次(按 owner 预算一次, 循环里查预算结果)。
- 评分标准(assert): fake execute_query 调用计数 == 1(单 owner 一次), 非 == 3(每表一次)。
- 自动脚本测试逻辑: sqlite in-memory engine; 3 表同 owner UPC; fake_exec 计数; build 后断言
  calls['n'] == 1。
"""
from sqlalchemy import create_engine

from contextos.config_dim.schema import metadata
from contextos.config_dim.pipeline import build_config_dimension
from contextos.config_dim.tests.test_pipeline_full import _ProfileStub


def test_path_b_one_query_per_owner(tmp_path):
    (tmp_path / "a.properties").write_text("x=1\n", encoding="utf-8")
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    calls = {"n": 0}

    def fake_exec(db, sql, **kw):
        calls["n"] += 1
        return [{"OWNER": "UPC", "TABLE_NAME": "SYS_CONFIG", "COMMENTS": "配置表"}]

    oracle_tables = [  # 3 表同 owner UPC -> path B 应只查 1 次(批量), 非 3 次
        {"owner": "UPC", "table": "SYS_CONFIG", "columns": [], "row_count": 1},
        {"owner": "UPC", "table": "T2", "columns": [], "row_count": 1},
        {"owner": "UPC", "table": "T3", "columns": [], "row_count": 1},
    ]
    build_config_dimension(repo_root=tmp_path, profile=_ProfileStub(), engine=eng,
                           cache_dir=tmp_path, oracle_tables=oracle_tables,
                           execute_query=fake_exec, db="CTEST")
    assert calls["n"] == 1  # 单 owner 一次, 非每表一次
