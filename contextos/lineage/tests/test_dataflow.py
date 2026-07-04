"""D10 trace_method_dataflow 三路 fallback 测试。"""
from contextos.storage.db import make_engine
from contextos.lineage import store


def _eng_with_data():
    e = make_engine("sqlite://")
    store.create_all(e)
    store.write_edges(e, [
        dict(edge_id="E1", src_table="PM_OFFER_BASE", dst_table="PM_OFFER_CHA",
             relation_type="INSERT_SELECT", confidence="medium",
             src_db="CCRM3", src_owner="UPC", dst_db="CCRM3", dst_owner="UPC"),
    ])
    store.write_evidence(e, [
        dict(edge_id="E1", evidence_type="CODE_SQL",
             evidence_ref="order/impl/PmOfferDao.java:142", excerpt="INSERT ..."),
    ])
    store.write_templates(e, [
        dict(template_id="T1", source_file="order/impl/PmOfferDao.java",
             container="PmOfferDao.query", sql_text="SELECT * FROM OM_CUSTOMER_F",
             recovery_mode="literal", confidence="medium"),
    ])
    return e


def test_path_b_evidence_reverse_lookup():
    from contextos.lineage.dataflow import trace_method_dataflow
    eng = _eng_with_data()
    hits = trace_method_dataflow(eng, source_path="order/impl/PmOfferDao.java")
    tables = {h["table"] for h in hits}
    assert "PM_OFFER_CHA" in tables and "PM_OFFER_BASE" in tables
    b = [h for h in hits if h["table"] == "PM_OFFER_CHA"][0]
    assert b["source"] == "code-lineage-evidence-fallback"


def test_path_c_template_reverse_lookup():
    """B 无命中时, 路径 C 从 sql_templates 抽表。"""
    from contextos.lineage.dataflow import trace_method_dataflow
    eng = _eng_with_data()
    # 一个只在 template 出现、edge 不涉及的文件
    store.write_templates(eng, [
        dict(template_id="T2", source_file="crm/Other.java", container="Other.q",
             sql_text="SELECT * FROM CRM_USER_T", recovery_mode="literal", confidence="medium")])
    hits = trace_method_dataflow(eng, source_path="crm/Other.java")
    tables = {h["table"] for h in hits}
    assert "CRM_USER_T" in tables
    assert any(h["source"] == "code-sql-template-fallback" for h in hits)


def test_path_a_always_empty_v1():
    """路径 A(java_table_refs)v1 预期空, 不报错。"""
    from contextos.lineage.dataflow import trace_method_dataflow
    eng = _eng_with_data()
    hits = trace_method_dataflow(eng, source_path="nonexistent/File.java")
    assert hits == []  # 三路都无命中 -> 空(不抛)


def test_file_level_returns_all_methods_tables():
    """D10 是文件级(review Finding #3): 同文件多方法的 SQL 表全返回, 不按 method line 过滤。

    守住"method_line 死参数已删、文件级是诚实契约"——若有人重引入坏的行级过滤, 这条会挂。"""
    from contextos.lineage.dataflow import trace_method_dataflow
    e = make_engine("sqlite://")
    store.create_all(e)
    store.write_edges(e, [
        dict(edge_id="EA", src_table="TAB_A", dst_table="TAB_A2", relation_type="JOIN",
             confidence="medium"),
        dict(edge_id="EB", src_table="TAB_B", dst_table="TAB_B2", relation_type="JOIN",
             confidence="medium")])
    store.write_evidence(e, [
        dict(edge_id="EA", evidence_type="CODE_SQL", evidence_ref="dao/Multi.java:10", excerpt=""),
        dict(edge_id="EB", evidence_type="CODE_SQL", evidence_ref="dao/Multi.java:20", excerpt="")])
    hits = trace_method_dataflow(e, source_path="dao/Multi.java")
    tables = {h["table"] for h in hits}
    assert {"TAB_A", "TAB_B"} <= tables          # 两方法的表都在(文件级)


def test_path_b_underscore_in_path_no_false_match():
    """source_path 含下划线不被当 LIKE 通配符误匹配兄弟文件(reviewer Minor #2)。

    '_' 在 SQL LIKE 是单字符通配; Java 文件名 _ 很常见(My_Dao). 未转义会让 My_Dao.java
    的 D10 查询误命中 MyXDao.java 的 evidence -> 假表。"""
    from contextos.lineage.dataflow import trace_method_dataflow
    e = make_engine("sqlite://")
    store.create_all(e)
    store.write_edges(e, [dict(edge_id="E1", src_table="T_OTHER_FILE", dst_table="T_X",
                               relation_type="JOIN", confidence="medium")])
    # evidence 属于 MyXDao.java(下划线位置是别的字符), 不应被 My_Dao.java 的查询命中
    store.write_evidence(e, [dict(edge_id="E1", evidence_type="CODE_SQL",
                                  evidence_ref="dao/MyXDao.java:5", excerpt="")])
    hits = trace_method_dataflow(e, source_path="dao/My_Dao.java")
    assert hits == []          # '_' 不当通配符 -> 不误命中 MyXDao


def test_dedup_across_paths():
    from contextos.lineage.dataflow import trace_method_dataflow
    eng = _eng_with_data()
    hits = trace_method_dataflow(eng, source_path="order/impl/PmOfferDao.java")
    # 同表不重复(B 已命中 PM_OFFER_CHA, C 即便也抽到也只留一条)
    targets = [h["table"] for h in hits]
    assert len(targets) == len(set(targets))


def test_trace_fresh_db_degrades_to_empty():
    """fresh 库(血缘表族未建, 如只跑过 init --only code): 视同空血缘返 [], 不裸抛。"""
    from contextos.lineage.dataflow import trace_method_dataflow
    e = make_engine("sqlite://")
    assert trace_method_dataflow(e, source_path="order/impl/PmOfferDao.java") == []
