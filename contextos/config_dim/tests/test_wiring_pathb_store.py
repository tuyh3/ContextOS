"""路 B store 化(去 live SQL): 从注入的表注释识别, 不再 execute_query(spec 附录 D.5, L3)。

设计思路(memory feedback_contextos_test_documentation):
- D.5 前: 路 B 是 `ALL_TAB_COMMENTS WHERE owner IN (...)` 的 live 查询, 需注入 execute_query,
  且旧 build 按 owner 预算一次(O(owners) 批量优化)。D.5 后: 注释随表清单注入(t["comment"],
  源自 store table_metadata.comment, 方言无关), 路 B 逐表纯函数判定, 整条 live SQL + 批量优化
  一并消失, execute_query 只剩 W7 行快照(Oracle-only 休眠)在用。
- 本 task 验证: 不传 execute_query, 仅凭注入的注释就能让路 B 识别配置表(证明去 live SQL 化成立)。
  为隔离路 B, 关掉 path A 表名启发(name_patterns=[]) —— 命中只可能来自注释。
- 评分标准(assert): 注释含配置信号词 '配置' 的表被路 B 消费。单路 B(权重 0.40)-> score 0.40
  落 needs_review(0.3<=score<0.6, design §5.5), 故断言 config_tables_needs_review==1; 无注释的
  对照表不被识别。
- 自动脚本测试逻辑: sqlite in-memory engine; 注入两张表(一张有配置注释、一张普通注释)+ 无
  execute_query; build 后断言只有带配置注释的进 needs_review。
"""
from sqlalchemy import create_engine

from contextos.config_dim.schema import metadata
from contextos.config_dim.pipeline import build_config_dimension
from contextos.config_dim.tests.test_pipeline_full import _ProfileStub


def test_path_b_identifies_from_comment_without_execute_query(tmp_path):
    (tmp_path / "a.properties").write_text("x=1\n", encoding="utf-8")
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    prof = _ProfileStub()
    prof.config_tables.detection.name_patterns = []   # 关 path A, 隔离路 B(命中只来自注释)

    oracle_tables = [
        {"owner": "UPC", "table": "PM_OFFER_CHA", "columns": [], "row_count": 1,
         "comment": "Offer 渠道配置表"},                 # 注释含 '配置' -> 路 B 命中
        {"owner": "UPC", "table": "PM_TRADE_LOG", "columns": [], "row_count": 1,
         "comment": "交易流水表"},                        # 普通注释 -> 不命中
    ]
    stats = build_config_dimension(
        repo_root=tmp_path, profile=prof, engine=eng, cache_dir=tmp_path,
        oracle_tables=oracle_tables, db="CTEST")        # 注意: 不传 execute_query

    # 单路 B(0.40)-> needs_review; 只有带配置注释的那张被识别
    assert stats["config_tables_needs_review"] == 1


def test_path_b_no_comment_no_hit(tmp_path):
    # 表清单无 comment 字段(或空)-> 路 B 零命中(不误报), 只有 Phase A 文件源
    (tmp_path / "a.properties").write_text("x=1\n", encoding="utf-8")
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    prof = _ProfileStub()
    prof.config_tables.detection.name_patterns = []
    oracle_tables = [{"owner": "UPC", "table": "PM_TRADE_LOG", "columns": [], "row_count": 1}]
    stats = build_config_dimension(
        repo_root=tmp_path, profile=prof, engine=eng, cache_dir=tmp_path,
        oracle_tables=oracle_tables, db="CTEST")
    assert stats.get("config_tables", 0) == 0
    assert stats.get("config_tables_needs_review", 0) == 0
