"""W1: rule_sets populate 接线(build_config_dimension -> identify.identify_rule_set)。

设计思路(memory feedback_contextos_test_documentation):
- Plan 06 落了纯逻辑 identify.identify_rule_set(>=2 规则列 -> rule_set), 但 C5 pipeline
  build 未把它接进编排, rule_sets 表始终空(Plan 06 复盘抓到的 build wiring gap)。
- 本 task 验证: build_config_dimension 对注入的 oracle_tables 跑 identify_rule_set, 命中
  (>=2 规则列)的表落 rule_sets 行。
- 评分标准(assert): build 后 select rule_sets, 有一行 name == 'CB_PRICING_RULE'
  (该表 columns 含 EFFECTIVE_DATE+STATUS, 是默认 rule_columns 里 >=2 个 -> rule_set)。
- 自动脚本测试逻辑: sqlite in-memory engine + metadata.create_all; 注入 oracle_tables 静态
  清单(无 execute_query/rag_search, 纯 path A + rule_set 离线判定)。build 后 select。
"""
from sqlalchemy import create_engine, select

from contextos.config_dim.schema import metadata, rule_sets
from contextos.config_dim.pipeline import build_config_dimension
from contextos.config_dim.tests.test_pipeline_full import _ProfileStub  # 复用 stub


def test_build_populates_rule_sets(tmp_path):
    (tmp_path / "a.properties").write_text("x=1\n", encoding="utf-8")
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    oracle_tables = [{"owner": "UPC", "table": "CB_PRICING_RULE",
                      "columns": ["EFFECTIVE_DATE", "STATUS", "AMOUNT"], "row_count": 5}]
    build_config_dimension(repo_root=tmp_path, profile=_ProfileStub(), engine=eng,
                           cache_dir=tmp_path, oracle_tables=oracle_tables, db="CTEST")
    with eng.connect() as c:
        rs = list(c.execute(select(rule_sets)))
    assert any(r.name == "CB_PRICING_RULE" for r in rs)  # >=2 规则列 -> rule_set
