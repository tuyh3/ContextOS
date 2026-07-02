"""Layer 5-6 sqlglot 解析测试。"""


def test_preprocess_placeholders():
    from contextos.lineage.sql_parse import preprocess_sql
    assert preprocess_sql("SELECT * FROM {UM_EC_VPN};") == "SELECT * FROM UM_EC_VPN"
    assert ":var" in preprocess_sql("SELECT * FROM T WHERE x = ${var}")
    assert ":p" in preprocess_sql("SELECT * FROM T WHERE x = ?")


def test_insert_select_write_relation():
    from contextos.lineage.sql_parse import parse_sql
    rels, _seq, err = parse_sql(
        "INSERT INTO PM_OFFER_CHA SELECT * FROM PM_OFFER_BASE")
    assert err is None
    ins = [r for r in rels if r.relation_type == "INSERT_SELECT"]
    assert ins and ins[0].dst_table == "PM_OFFER_CHA"
    assert ins[0].src_table == "PM_OFFER_BASE"


def test_join_relation():
    from contextos.lineage.sql_parse import parse_sql
    rels, _seq, err = parse_sql(
        "SELECT a.id FROM TAB_A a JOIN TAB_B b ON a.id = b.aid")
    assert err is None
    joins = [r for r in rels if r.relation_type == "JOIN"]
    assert joins
    tables = {joins[0].src_table, joins[0].dst_table}
    assert tables == {"TAB_A", "TAB_B"}


def test_where_eq_relation():
    from contextos.lineage.sql_parse import parse_sql
    rels, _seq, err = parse_sql(
        "SELECT * FROM TAB_A a, TAB_B b WHERE a.id = b.aid")
    assert err is None
    assert any(r.relation_type == "WHERE_EQ" for r in rels)


def test_subquery_and_exists():
    from contextos.lineage.sql_parse import parse_sql
    rels, _seq, _err = parse_sql(
        "SELECT * FROM TAB_A a WHERE a.id IN (SELECT id FROM TAB_B)")
    assert any(r.relation_type == "SUBQUERY" for r in rels)
    rels2, _s2, _e2 = parse_sql(
        "SELECT * FROM TAB_A a WHERE EXISTS (SELECT 1 FROM TAB_B b WHERE b.aid = a.id)")
    # #3 收紧(2026-06-02 审计): EXISTS 实测确切产 EXISTS(TAB_B->TAB_A);
    # 原 `in ("EXISTS","WHERE_EQ")` 太松 -> EXISTS 检测坏掉、相关谓词漏成 WHERE_EQ 也能过。
    assert any(r.relation_type == "EXISTS" for r in rels2)
    assert not any(r.relation_type == "WHERE_EQ" for r in rels2)


def test_sequence_ref():
    from contextos.lineage.sql_parse import parse_sql
    _rels, seq, _err = parse_sql(
        "INSERT INTO T_ORDER (ID) VALUES (SEQ_ORDER.NEXTVAL)")
    assert seq and seq[0].sequence_name == "SEQ_ORDER"
    assert seq[0].context_table == "T_ORDER"


def test_garbage_returns_error():
    from contextos.lineage.sql_parse import parse_sql
    rels, seq, err = parse_sql("NOT SQL AT ALL @@@ ###")
    assert err is not None
    assert rels == [] and seq == []


def test_insert_captures_target_schema():
    """显式 schema 的 INSERT 目标表 owner 不丢(review HIGH: dst_schema 未透传)。"""
    from contextos.lineage.sql_parse import parse_sql
    rels, _, _ = parse_sql("INSERT INTO UPC.T_DST SELECT * FROM SEC.T_SRC")
    assert len(rels) == 1
    r = rels[0]
    assert (r.src_schema, r.src_table) == ("SEC", "T_SRC")
    assert (r.dst_schema, r.dst_table) == ("UPC", "T_DST")


def test_cross_schema_same_name_join_not_dropped():
    """两个不同 schema 的同名表 JOIN 不被当自连过滤(review HIGH: 显式 schema 区分身份)。"""
    from contextos.lineage.sql_parse import parse_sql
    rels, _, _ = parse_sql("SELECT * FROM UPC.COMMON_T a JOIN SEC.COMMON_T b ON a.id=b.id")
    joins = [r for r in rels if r.relation_type == "JOIN"]
    assert len(joins) == 1
    assert {joins[0].src_schema, joins[0].dst_schema} == {"UPC", "SEC"}
    assert joins[0].src_table == "COMMON_T" and joins[0].dst_table == "COMMON_T"


def test_same_table_self_join_still_filtered():
    """同一张表自连(无 schema 区分)仍被过滤, 不产关系。"""
    from contextos.lineage.sql_parse import parse_sql
    rels, _, _ = parse_sql("SELECT * FROM T_X a JOIN T_X b ON a.pid=b.id")
    assert [r for r in rels if r.relation_type == "JOIN"] == []


def test_subquery_cross_schema_same_name_captured():
    """子查询 IN 同名跨 schema: 不被当同表过滤 + 带 schema(review HIGH 完整性, 路径 5)。"""
    from contextos.lineage.sql_parse import parse_sql
    rels, _, _ = parse_sql(
        "SELECT * FROM UPC.COMMON_T a WHERE a.id IN (SELECT b.id FROM SEC.COMMON_T b)")
    subs = [r for r in rels if r.relation_type == "SUBQUERY"]
    assert len(subs) == 1
    assert {subs[0].src_schema, subs[0].dst_schema} == {"UPC", "SEC"}


def test_exists_cross_schema_same_name_captured():
    """EXISTS 同名跨 schema(路径 6)。"""
    from contextos.lineage.sql_parse import parse_sql
    rels, _, _ = parse_sql(
        "SELECT * FROM UPC.COMMON_T a WHERE EXISTS (SELECT 1 FROM SEC.COMMON_T b WHERE b.pid=a.id)")
    ex = [r for r in rels if r.relation_type == "EXISTS"]
    assert len(ex) == 1
    assert {ex[0].src_schema, ex[0].dst_schema} == {"UPC", "SEC"}


def test_insert_self_name_cross_schema_not_filtered():
    """INSERT INTO UPC.COMMON_T SELECT FROM SEC.COMMON_T: 跨 schema 同名不被 write 自连过滤(路径 7)。"""
    from contextos.lineage.sql_parse import parse_sql
    rels, _, _ = parse_sql("INSERT INTO UPC.COMMON_T SELECT * FROM SEC.COMMON_T")
    ins = [r for r in rels if r.relation_type == "INSERT_SELECT"]
    assert len(ins) == 1
    assert (ins[0].src_schema, ins[0].dst_schema) == ("SEC", "UPC")


def test_normal_subquery_diff_name_still_works():
    """普通子查询(不同名, 裸): schema 空, 关系仍产 —— 守住非回归。"""
    from contextos.lineage.sql_parse import parse_sql
    rels, _, _ = parse_sql("SELECT * FROM OUTER_T a WHERE a.id IN (SELECT b.oid FROM INNER_T b)")
    subs = [r for r in rels if r.relation_type == "SUBQUERY"]
    assert len(subs) == 1
    assert subs[0].src_table == "INNER_T" and subs[0].dst_table == "OUTER_T"


def test_ctas_maps_to_insert_select():
    """CREATE TABLE X AS SELECT FROM Y -> relation_type=INSERT_SELECT(不是 CTAS, 不退化 WHERE_EQ)。"""
    from contextos.lineage.sql_parse import parse_sql
    relations, _seq, error = parse_sql("CREATE TABLE T_NEW AS SELECT * FROM T_SRC")
    assert error is None
    rels = [r for r in relations if r.dst_table.upper() == "T_NEW"]
    assert rels, "应有写 T_NEW 的关系"
    assert all(r.relation_type == "INSERT_SELECT" for r in rels)
    assert all(r.relation_type != "CTAS" for r in relations)   # 不再产第 9 种
