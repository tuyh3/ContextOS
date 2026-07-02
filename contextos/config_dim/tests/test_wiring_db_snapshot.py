"""W7: db_snapshot 接线 + build_datasource_map + _dskey_from_url + 末调 backfill_owners。

设计思路(memory feedback_contextos_test_documentation):
- Plan 06 落了纯逻辑 db_snapshot.snapshot_small/snapshot_big + owner_backfill.backfill_owners,
  但 C5 build 未把它们接进编排: 识别出的 high/confirmed config_table 没拉 DB 行快照,
  owner overlay 也没接线。本 task 补这层 wiring(Plan 06 复盘抓到的最后一段 build gap)。
- 评分标准(assert), 5 个:
  1. test_db_snapshot_writes_config_items_rows: 小表(rc<=阈值)high config_table -> SELECT * 全量
     快照, 每行一条 config_items(value_type='row')。HIGH 安全: db 是连接选择器(execute_query
     首参), 绝不进 Oracle 表名(只 owner.table 两段, Oracle 三段 schema.table.column 真库非法)。
  2. test_db_snapshot_big_table_group_by_wiring: 大表(rc>big_table_row_threshold)-> GROUP BY +
     snapshot_big 落 count/summary 行(非小表 'row')。
  3. test_build_datasource_map_ambiguous_module_skipped(MED 1): 同 module 两 jdbc.username 冲突
     -> ambiguous -> 跳过(不串库错身份回填)。
  4. test_build_datasource_map_single_module_dskey_from_url: 单 module 单身份 -> user 取 username,
     datasource_key 从明文 url 末段提。
  5. test_build_datasource_map_skips_masked_url(LOW): 内嵌凭据 url 被整值打码(is_sensitive=1)->
     不从 ****xxxx 提垃圾 dskey, dskey 留空只保 user。
- 自动脚本测试逻辑: sqlite in-memory; fake_exec 按 SQL 形态分支(ALL_TAB_COMMENTS / SELECT * /
  GROUP BY)+ fake_search 凑 path C 第三路让 fuse>=high(snapshot 仅 high/confirmed 触发);
  build_datasource_map 用直接造 config_sources/config_items 行的单元测试(不走全 build)。
"""
from sqlalchemy import create_engine, select

from contextos.config_dim.schema import metadata, config_items
from contextos.config_dim.pipeline import build_config_dimension
from contextos.config_dim.tests.test_pipeline_full import _ProfileStub


def test_db_snapshot_writes_config_items_rows(tmp_path):
    (tmp_path / "a.properties").write_text("x=1\n", encoding="utf-8")
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)

    def fake_exec(db, sql, **kw):
        if "ALL_TAB_COMMENTS" in sql:
            return [{"OWNER": "UPC", "TABLE_NAME": "SYS_CONFIG", "COMMENTS": "配置表"}]
        if "SYS_CONFIG" in sql and "SELECT *" in sql.upper():  # 小表全量快照
            # HIGH: db 是连接/实例选择器(本函数首参), 绝不进 Oracle 表名; 只 owner.table 两段。
            #       Oracle 三段是 schema.table.column -> 'CTEST.UPC.SYS_CONFIG' 真库非法 SQL。
            assert "UPC.SYS_CONFIG" in sql and "CTEST." not in sql
            return [{"K": "A", "V": "1"}, {"K": "B", "V": "2"}]
        return []

    # snapshot 仅 high/confirmed 触发; path B 单路 .4 + path A .1 = 0.5 只到 needs_review,
    # 必须凑第三路让 fuse >=0.6 且 >=2 路 -> high。fake_search 命中 path C(行含"配置"信号词):
    class _H:
        def __init__(s, line):
            s.line = line
            s.rel_path = "d/a.md"

    def fake_search(patterns, subsets):
        return [_H("配置 说明")] if "business_docs" in subsets else []

    oracle_tables = [{"owner": "UPC", "table": "SYS_CONFIG", "columns": [], "row_count": 2,
                      "pk_cols": ["K"]}]
    build_config_dimension(repo_root=tmp_path, profile=_ProfileStub(), engine=eng,
                           cache_dir=tmp_path, oracle_tables=oracle_tables,
                           execute_query=fake_exec, rag_search=fake_search, db="CTEST")
    with eng.connect() as c:
        items = list(c.execute(select(config_items).where(config_items.c.value_type == "row")))
    assert len(items) == 2  # SYS_CONFIG 2 行落 config_items(DB 行, value_type='row')


def test_db_snapshot_big_table_group_by_wiring(tmp_path):
    """MEDIUM: 大表(row_count > big_table_row_threshold=50000)走 GROUP BY + snapshot_big 落
    count/summary 行(非小表 SELECT * 的 'row')。中性合成 fixture。"""
    (tmp_path / "a.properties").write_text("x=1\n", encoding="utf-8")
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)

    def fake_exec(db, sql, **kw):
        if "ALL_TAB_COMMENTS" in sql:
            return [{"OWNER": "APP1", "TABLE_NAME": "APP_PARAM", "COMMENTS": "配置表"}]
        if "GROUP BY" in sql.upper():
            assert "TESTDB." not in sql and "APP1.APP_PARAM" in sql  # db 不进 Oracle 表名
            return [{"K": "A", "CNT": 30}, {"K": "B", "CNT": 20}]
        return []

    class _H:
        def __init__(s, line):
            s.line = line
            s.rel_path = "d/a.md"

    def fake_search(patterns, subsets):  # path C 凑第三路 -> high(snapshot 才触发)
        return [_H("配置 说明")] if "business_docs" in subsets else []

    oracle_tables = [{"owner": "APP1", "table": "APP_PARAM", "columns": [], "row_count": 50001,
                      "pk_cols": ["K"]}]
    build_config_dimension(repo_root=tmp_path, profile=_ProfileStub(), engine=eng,
                           cache_dir=tmp_path, oracle_tables=oracle_tables,
                           execute_query=fake_exec, rag_search=fake_search, db="TESTDB")
    with eng.connect() as c:
        items = list(c.execute(select(config_items).where(
            config_items.c.value_type.in_(["count", "summary"]))))
    assert any(i.value_type == "count" for i in items)    # 每枚举值一条
    assert any(i.value_type == "summary" for i in items)  # _summary 汇总行


def test_build_datasource_map_ambiguous_module_skipped():
    """MED 1: 同 module 两个不同 jdbc.username -> ambiguous -> 跳过(不串库错身份回填)。"""
    from contextos.config_dim.pipeline import build_datasource_map
    from contextos.config_dim.schema import config_sources, config_items as ci
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(config_sources.insert().values(source_id="s1", source_type="file",
                                                 file_path="cust/db1.properties"))
        c.execute(config_sources.insert().values(source_id="s2", source_type="file",
                                                 file_path="cust/db2.properties"))
        c.execute(ci.insert().values(item_id="i1", source_id="s1", entity_id="", snapshot_id="x",
                                     config_key="jdbc.username", key_path="jdbc.username",
                                     value_raw="ET", value_type="str"))
        c.execute(ci.insert().values(item_id="i2", source_id="s2", entity_id="", snapshot_id="x",
                                     config_key="jdbc.username", key_path="jdbc.username",
                                     value_raw="ORD", value_type="str"))
    assert "cust" not in build_datasource_map(eng)  # 两 username 冲突 -> module 跳过


def test_build_datasource_map_single_module_dskey_from_url():
    """正样本: 单 module 单身份 -> user 取 username, datasource_key 从 url 末段提。"""
    from contextos.config_dim.pipeline import build_datasource_map
    from contextos.config_dim.schema import config_sources, config_items as ci
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(config_sources.insert().values(source_id="s1", source_type="file",
                                                 file_path="cust/jdbc.properties"))
        c.execute(ci.insert().values(item_id="i1", source_id="s1", entity_id="", snapshot_id="x",
                                     config_key="jdbc.username", key_path="jdbc.username",
                                     value_raw="ET", value_type="str"))
        c.execute(ci.insert().values(item_id="i2", source_id="s1", entity_id="", snapshot_id="x",
                                     config_key="jdbc.url", key_path="jdbc.url",
                                     value_raw="jdbc:oracle:thin:@host/crmdev1", value_type="str"))
    dmap = build_datasource_map(eng)
    assert dmap["cust"] == {"user": "ET", "datasource_key": "crmdev1"}


def test_build_datasource_map_skips_masked_url():
    """LOW: 内嵌凭据 url 被整值打码(is_sensitive=1)-> 不从 ****xxxx 提垃圾 dskey, dskey 留空只保 user。
    (中性合成 fixture)"""
    from contextos.config_dim.pipeline import build_datasource_map
    from contextos.config_dim.schema import config_sources, config_items as ci
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(config_sources.insert().values(source_id="s1", source_type="file",
                                                 file_path="mod_a/jdbc.properties"))
        c.execute(ci.insert().values(item_id="i1", source_id="s1", entity_id="", snapshot_id="x",
                                     config_key="jdbc.username", key_path="jdbc.username",
                                     value_raw="APPUSER", value_type="str"))
        c.execute(ci.insert().values(item_id="i2", source_id="s1", entity_id="", snapshot_id="x",
                                     config_key="jdbc.url", key_path="jdbc.url",
                                     value_raw="****t123", value_type="str", is_sensitive=1))
    dmap = build_datasource_map(eng)
    assert dmap["mod_a"] == {"user": "APPUSER", "datasource_key": ""}  # masked url skip, 只留 user
