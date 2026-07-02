"""全 build 编排端到端(离线 + fakes)。Task C5。

设计思路(memory feedback_contextos_test_documentation):
- build_config_dimension 把三 Phase 串成一条链, 离线只验主链(真跑覆盖见 C6 integration):
  * Phase A: build_file_config 扫文件配置 -> config_sources(source_type='file')。
  * Phase B: 对注入的 oracle_tables 清单跑四路识别(path A 表名启发 / path B Oracle
    DDL COMMENT 走 execute_query / path C+D RAG sparse 走 rag_search)-> fuse_config_table
    融合 -> 命中 high 的写 config_sources(source_type='db_table')。
  * Phase C: apply_confirmations 权威覆盖(human_confirmed > 自动); 此用例无 confirmation
    记录, 走自动 verdict。
- 评分标准(assert):
  1. config_sources 同时含 source_type 'file'(Phase A 文件源)和 'db_table'(Phase B 识别
     的配置表源)。
  2. stats['config_tables'] >= 1(SYS_CONFIG 被四路融合判 high)。
     fake 设计: path A 命中(name_patterns 含 CONFIG / 规则列 EFFECTIVE_DATE+STATUS >=2)+
     path B 命中(ALL_TAB_COMMENTS 返 '系统配置表' 含 zh 关键词 '配置')+ path C 命中
     (business_docs 命中 'SYS_CONFIG 系统配置表')-> score>=0.6 且 >=2 路 -> high。
- 自动脚本测试逻辑: sqlite in-memory engine + metadata.create_all; 注入 oracle_tables 静态
  清单 + fake_exec(模拟 05 §8.2 execute_query 返 ALL_TAB_COMMENTS 行)+ fake_search(模拟
  03b sparse scoped_hits)。build 后 select config_sources 断言。engine_05 不给 -> 不回写。

蓝本偏离(deviations):plan 蓝本 test 用 `Profile()`, 但真实 Profile 有多个无默认值必填
字段(llm/embedding/storage/...), `Profile()` 无参构造会 ValidationError。build_config_dimension
只读 `profile.config`(Phase A)+ `profile.config_tables.detection`(Phase B), 故沿用
test_pipeline_phase_a.py 的最小 stand-in 做法, 暴露真实 ConfigConfig()/ConfigTablesConfig()
(带真实默认词表/扩展名/规则列), 既不改函数签名也不硬凑。
"""
from pathlib import Path

from sqlalchemy import create_engine, select

from contextos.config_dim.pipeline import build_config_dimension
from contextos.config_dim.schema import metadata, config_sources
from contextos.profile.schema import ConfigConfig, ConfigTablesConfig


class _ProfileStub:
    """build_config_dimension 只触碰 .config(Phase A)+ .config_tables(Phase B)。"""

    def __init__(self) -> None:
        self.config = ConfigConfig()
        self.config_tables = ConfigTablesConfig()
        # 让 path A 默认 name_patterns(通用中立空)叠上 CONFIG, 保证 SYS_CONFIG 命中表名。
        self.config_tables.detection.name_patterns = ["CONFIG"]


class _Hit:
    def __init__(self, line: str) -> None:
        self.line = line
        self.rel_path = "activity_document/a.md"


def test_full_build_offline_with_fakes(tmp_path):
    (tmp_path / "application.properties").write_text("app.url=http://h\n", encoding="utf-8")
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    # Phase B fakes: oracle 表清单 + execute_query(DDL) + search(RAG)
    oracle_tables = [{
        "owner": "UPC", "table": "SYS_CONFIG",
        "columns": ["EFFECTIVE_DATE", "STATUS"], "row_count": 10,
    }]

    def fake_exec(db, sql, **kw):
        if "ALL_TAB_COMMENTS" in sql:
            return [{"OWNER": "UPC", "TABLE_NAME": "SYS_CONFIG", "COMMENTS": "系统配置表"}]
        return []

    def fake_search(patterns, subsets):
        if "business_docs" in subsets:
            return [_Hit("SYS_CONFIG 系统配置表")]
        return []

    stats = build_config_dimension(
        repo_root=tmp_path, profile=_ProfileStub(), engine=eng, cache_dir=tmp_path,
        oracle_tables=oracle_tables, execute_query=fake_exec, rag_search=fake_search,
        db="CTEST", customer_id="demoproj")

    # 文件源 + DB config_table 源都写了
    with eng.connect() as c:
        srcs = list(c.execute(select(config_sources)))
    types = {s.source_type for s in srcs}
    assert "file" in types and "db_table" in types
    assert stats["config_tables"] >= 1


def test_full_build_populates_all_tables(tmp_path):
    """W8: 全编排一条龙触发 W1-W7 全接线 —— rule_sets / config_evidence / config_items(DB 行) /
    owner_resolution 都 populate(全注入齐: oracle_tables+execute_query+rag_search+engine_05+
    synonym_lookup)。

    设计思路(memory feedback_contextos_test_documentation):
    - 此用例把 W1(rule_sets)/W5(config_evidence excerpt 过 sanitize)/W7(db_snapshot 小表 SELECT *
      落 config_items value_type='row')/W6(owner overlay 回填 owner_resolution)四条 wiring 在
      build_config_dimension 一条龙下全验, 是 06c-wiring 的集成验收(单功能各自见 W1-W7 守护测试)。
    - SYS_CONFIG fake: path A 命中(name_patterns CONFIG + 规则列 EFFECTIVE_DATE+STATUS >=2)+ path B
      DDL COMMENT '配置表' 含 zh 信号词 + path C business_docs 命中行含 '配置' -> fuse >=0.6 且 >=2
      路 -> high -> 写 db_table 源 + 触发 W5 证据 + W7 快照。
    - 评分标准(assert): 4 张表均非空。
      * rule_sets(W1): SYS_CONFIG 有 >=2 规则列 -> identify_rule_set 返表级规则集。
      * config_evidence(W5): path B/C 命中 dict -> 落证据行(excerpt 过 sanitize_text)。
      * config_items value_type='row'(W7): high 表 + row_count<=阈值 -> SELECT * -> snapshot_small
        每行一条 value_type='row'。fake_exec 对非 ALL_TAB_COMMENTS 返一行 {"K":"A","V":"1"}。
      * owner_resolution(W6): engine_05 有裸名边(src/dst_owner='')+ Phase A 落 jdbc.username -> module
        'cust' -> datasource_map['cust'] -> resolve_side(user, table, synonym_lookup) -> 写 overlay。
    - 自动脚本测试逻辑: 双 sqlite in-memory(e06 配置维度 + e05 lineage)+ 注入 4 个 fake/清单。
      e05 预置一条裸名边 E1(SYS_CONFIG->X)+ 一条 evidence(cust/Dao.java:1, module='cust')。
      jdbc.properties 落在 cust/ 子目录 -> module='cust' 对上 -> backfill 命中。
    """
    from sqlalchemy import create_engine, select
    from contextos.config_dim.schema import (
        metadata, rule_sets, config_evidence, config_items, owner_resolution)
    from contextos.lineage import store as L
    from contextos.config_dim.pipeline import build_config_dimension

    (tmp_path / "cust").mkdir()
    (tmp_path / "cust" / "jdbc.properties").write_text(
        "jdbc.username=ET\njdbc.url=jdbc:oracle:thin:@h/crmdev1\n", encoding="utf-8")
    e06 = create_engine("sqlite:///:memory:"); metadata.create_all(e06)
    e05 = create_engine("sqlite:///:memory:"); L.metadata.create_all(e05)
    with e05.begin() as c:
        c.execute(L.lineage_edges.insert().values(
            edge_id="E1", src_table="SYS_CONFIG", dst_table="X",
            src_owner="", dst_owner=""))
        c.execute(L.lineage_evidence.insert().values(
            edge_id="E1", evidence_ref="cust/Dao.java:1"))

    class _DbHit:
        def __init__(self, line):
            self.line = line
            self.rel_path = "activity_document/a.md"

    def fake_search(patterns, subsets):
        return [_DbHit("SYS_CONFIG 配置表")] if "business_docs" in subsets else []

    def fake_exec(db, sql, **kw):
        if "ALL_TAB_COMMENTS" in sql:
            return [{"OWNER": "UPC", "TABLE_NAME": "SYS_CONFIG", "COMMENTS": "配置表"}]
        return [{"K": "A", "V": "1"}]

    syn = lambda user, table: "PARTY" if table == "SYS_CONFIG" else None
    oracle_tables = [{"owner": "UPC", "table": "SYS_CONFIG",
                      "columns": ["EFFECTIVE_DATE", "STATUS"], "row_count": 1, "pk_cols": ["K"]}]

    build_config_dimension(
        repo_root=tmp_path, profile=_ProfileStub(), engine=e06, cache_dir=tmp_path,
        oracle_tables=oracle_tables, execute_query=fake_exec, rag_search=fake_search,
        db="CTEST", engine_05=e05, synonym_lookup=syn)

    with e06.connect() as c:
        assert list(c.execute(select(rule_sets)))                 # W1
        assert list(c.execute(select(config_evidence)))           # W5
        assert list(c.execute(                                    # W7
            select(config_items).where(config_items.c.value_type == "row")))
        assert list(c.execute(select(owner_resolution)))          # W6
