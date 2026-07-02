"""血缘表 SQLAlchemy 存储层测试。用内存 SQLite engine,不碰真库。"""
from contextos.storage.db import make_engine


def _engine():
    return make_engine("sqlite://")  # in-memory


def test_create_all_idempotent():
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    store.create_all(eng)  # 第二次不报错(checkfirst)


def test_write_and_read_edges():
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    rows = [
        dict(edge_id="E1", src_db="CCRM3", src_owner="UPC", src_table="PM_OFFER_BASE",
             src_col="ID", dst_db="CCRM3", dst_owner="UPC", dst_table="PM_OFFER_CHA",
             dst_col="CHA_VALUE", relation_type="INSERT_SELECT", lineage_type="DIRECT",
             src_dataset_type="TABLE", dst_dataset_type="TABLE", confidence="medium",
             evidence_count=3, recovery_mode="sql_file", branch_detected=False),
    ]
    store.write_edges(eng, rows)
    got = store.all_edges(eng)
    assert len(got) == 1
    assert got[0]["edge_id"] == "E1"
    assert got[0]["dst_table"] == "PM_OFFER_CHA"
    assert got[0]["branch_detected"] is False


def test_clear_all_then_rebuild():
    """build 是全量重建: clear_all 清空后重写。"""
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    store.write_edges(eng, [dict(edge_id="E1", relation_type="JOIN")])
    store.clear_all(eng)
    assert store.all_edges(eng) == []


def test_evidence_and_templates_and_unresolved():
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    store.write_evidence(eng, [dict(edge_id="E1", evidence_type="CODE_SQL",
                                    evidence_ref="a/B.sql:10", excerpt="SELECT ...",
                                    extractor_version="1.0.0")])
    store.write_templates(eng, [dict(template_id="T1", source_file="a/B.sql",
                                     container="", sql_text="SELECT 1 FROM DUAL",
                                     recovery_mode="sql_file", confidence="medium")])
    store.write_unresolved(eng, [dict(source_path="a/C.sql", line_start=5,
                                      recovery_mode="semicolon_split",
                                      reason="parse failed (AST + regex)",
                                      sql_excerpt="GAR BAGE")])
    assert store.evidence_for(eng, "E1")[0]["evidence_ref"] == "a/B.sql:10"
    assert store.all_templates(eng)[0]["template_id"] == "T1"
    assert store.count_unresolved(eng) == 1


def test_table_metadata_same_name_across_owners_both_persist():
    """裁决 5 身份锚 = owner.table: 同名表跨 owner 必须各存一行, 不撞 PK 不静默丢。

    回归 review Finding #1: table_metadata 单列 template_name PK -> 多 owner 同名表崩 UNIQUE。
    某电信客户测试库有 51 schema, COMMON_*/CONFIG_* 类同名表跨 schema 常见。
    """
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    store.write_table_metadata(eng, [
        dict(template_name="COMMON_T", db_name="", owner="UPC",
             comment="客户公共表", dataset_type="TABLE"),
        dict(template_name="COMMON_T", db_name="", owner="SEC",
             comment="权限公共表", dataset_type="TABLE"),
    ])
    rows = [r for r in store.all_table_metadata(eng) if r["template_name"] == "COMMON_T"]
    assert sorted(r["owner"] for r in rows) == ["SEC", "UPC"]   # 两行都在, 没压成一行
    assert {r["comment"] for r in rows} == {"客户公共表", "权限公共表"}


def test_metadata_tables_empty_by_default():
    """离线降级: 元数据表默认空, has_metadata=False。"""
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    assert store.all_table_metadata(eng) == []
    assert store.has_metadata(eng) is False
    store.write_table_metadata(eng, [dict(db_name="CCRM3", owner="UPC",
                                          template_name="PM_OFFER_CHA",
                                          comment="Offer 渠道授权表", dataset_type="TABLE")])
    assert store.has_metadata(eng) is True


def test_lineage_edges_has_edge_kind_and_lifecycle_columns():
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    # 不传 edge_kind / 生命周期列 -> 取默认值(向后兼容既有 build 写法)
    store.write_edges(eng, [dict(edge_id="E1", relation_type="JOIN")])
    got = store.all_edges(eng)[0]
    assert got["edge_kind"] == "SQL"          # 默认 SQL(静态血缘边)
    assert got["is_active"] is True
    assert got["first_seen_at"] == ""
    assert got["last_seen_at"] == ""
    assert got["source_fingerprint"] == ""


def test_lineage_edges_edge_kind_object_dependency():
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    store.write_edges(eng, [dict(edge_id="OD1", src_table="V_CUST", dst_table="CB_CUSTOMER",
                                 relation_type="", edge_kind="OBJECT_DEPENDENCY",
                                 src_dataset_type="VIEW", confidence="high")])
    got = store.all_edges(eng)[0]
    assert got["edge_kind"] == "OBJECT_DEPENDENCY"
    assert got["src_dataset_type"] == "VIEW"
    assert got["relation_type"] == ""


def test_object_metadata_tables_write_read():
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    store.write_columns(eng, [dict(owner="UPC", table_name="CB_CUSTOMER", column_name="CUST_ID",
                                   data_type="NUMBER", nullable="N", comment="客户ID",
                                   column_id=1, db_name="CCRM3")])
    store.write_indexes(eng, [dict(owner="UPC", index_name="IDX_CUST", table_name="CB_CUSTOMER",
                                   uniqueness="UNIQUE", column_list="CUST_ID", db_name="CCRM3")])
    store.write_constraints(eng, [dict(owner="UPC", constraint_name="PK_CUST", table_name="CB_CUSTOMER",
                                       constraint_type="P", r_owner="", r_constraint_name="",
                                       search_condition="", db_name="CCRM3")])
    store.write_sequences(eng, [dict(owner="UPC", sequence_name="SEQ_CUST", min_value="1",
                                     max_value="9999999999", increment_by="1", last_number="42",
                                     cache_size="20", cycle_flag="N", db_name="CCRM3")])
    store.write_views(eng, [dict(owner="UPC", view_name="V_CUST", comment="", db_name="CCRM3")])
    store.write_procedures(eng, [dict(owner="UPC", object_name="PKG_CUST", object_type="PACKAGE",
                                      db_name="CCRM3")])
    store.write_dependencies(eng, [dict(owner="UPC", name="V_CUST", type="VIEW",
                                        referenced_owner="UPC", referenced_name="CB_CUSTOMER",
                                        referenced_type="TABLE", referenced_link_name="",
                                        db_name="CCRM3")])
    assert store.all_columns(eng)[0]["column_name"] == "CUST_ID"
    assert store.all_indexes(eng)[0]["index_name"] == "IDX_CUST"
    assert store.all_constraints(eng)[0]["constraint_type"] == "P"
    assert store.all_sequences(eng)[0]["last_number"] == "42"
    assert store.all_views(eng)[0]["view_name"] == "V_CUST"
    assert store.all_procedures(eng)[0]["object_type"] == "PACKAGE"
    deps = store.all_dependencies(eng)
    assert deps[0]["referenced_name"] == "CB_CUSTOMER"


def test_mixed_batch_missing_first_explicit_second_preserves_explicit():
    """混批 insert 不得静默覆盖显式值。

    回归 Task 1 review landmine: edge_kind/生命周期列 caller-optional + Python-side default 后,
    一个 write_edges batch 里第一行不带 edge_kind、第二行带 edge_kind=OBJECT_DEPENDENCY,
    SQLAlchemy 旧路径(conn.execute(insert, rows) 单条 executemany 用首行 keys 编译)会
    静默把显式 OBJECT_DEPENDENCY 压成默认 'SQL'(数据损坏)。混批是设计内现实
    (静态 pipeline 不带 edge_kind / object_lineage 带), 必须按行归一 + 补默认。
    """
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    store.write_edges(eng, [
        dict(edge_id="A1", relation_type="JOIN"),                           # 不带 edge_kind
        dict(edge_id="A2", relation_type="", edge_kind="OBJECT_DEPENDENCY"),  # 显式
    ])
    got = {r["edge_id"]: r["edge_kind"] for r in store.all_edges(eng)}
    assert got["A1"] == "SQL"                 # 缺省取默认
    assert got["A2"] == "OBJECT_DEPENDENCY"   # 显式值不被首行 keys 覆盖


def test_mixed_batch_explicit_first_missing_second_no_crash():
    """混批 insert 反向顺序不得硬崩。

    回归 Task 1 review landmine: 显式行在前、缺省行在后时, 旧路径抛
    InvalidRequestError: A value is required for bind parameter 'edge_kind'。
    按行补默认后, 缺省行应取默认 'SQL', 不崩。
    """
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    store.write_edges(eng, [
        dict(edge_id="B1", relation_type="", edge_kind="OBJECT_DEPENDENCY"),
        dict(edge_id="B2", relation_type="JOIN"),                            # 不带 edge_kind
    ])
    got = {r["edge_id"]: r["edge_kind"] for r in store.all_edges(eng)}
    assert got["B1"] == "OBJECT_DEPENDENCY"
    assert got["B2"] == "SQL"


def test_mixed_batch_lifecycle_columns_per_row_defaults():
    """混批补默认覆盖全部 caller-optional 列(不止 edge_kind), 含生命周期列。"""
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    store.write_edges(eng, [
        dict(edge_id="L1", is_active=False, first_seen_at="2026-06-06",
             edge_kind="OBJECT_DEPENDENCY"),
        dict(edge_id="L2", relation_type="JOIN"),   # 全缺省: edge_kind/生命周期都不传
    ])
    got = {r["edge_id"]: r for r in store.all_edges(eng)}
    assert got["L1"]["is_active"] is False
    assert got["L1"]["first_seen_at"] == "2026-06-06"
    assert got["L1"]["edge_kind"] == "OBJECT_DEPENDENCY"
    assert got["L2"]["is_active"] is True            # 默认
    assert got["L2"]["first_seen_at"] == ""          # 默认
    assert got["L2"]["edge_kind"] == "SQL"           # 默认


def test_mixed_batch_partial_non_default_column_uniform_keys():
    """某列无 Python 默认(如 evidence.edge_id), 行间提供不一致时不得崩。

    第一行带 edge_id、第二行不带 -> 旧 executemany 路径会因 keys 不齐崩。
    归一后缺省补 None(无 default 列), 不崩。
    """
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    store.write_evidence(eng, [
        dict(edge_id="E1", evidence_type="CODE_SQL", evidence_ref="a/B.sql:10"),
        dict(evidence_type="CODE_SQL", evidence_ref="a/C.sql:20"),   # 不带 edge_id
    ])
    refs = sorted(r["evidence_ref"] for r in store.all_evidence(eng))
    assert refs == ["a/B.sql:10", "a/C.sql:20"]


def test_clear_object_metadata_and_object_edges():
    from contextos.lineage import store
    eng = _engine()
    store.create_all(eng)
    store.write_views(eng, [dict(owner="UPC", view_name="V_CUST", comment="", db_name="CCRM3")])
    store.write_edges(eng, [dict(edge_id="OD1", edge_kind="OBJECT_DEPENDENCY", relation_type=""),
                            dict(edge_id="E2", edge_kind="SQL", relation_type="JOIN")])
    store.clear_object_metadata(eng)
    assert store.all_views(eng) == []
    store.clear_object_edges(eng)            # 只清 OBJECT_DEPENDENCY, 保留 SQL 边
    ids = {e["edge_id"] for e in store.all_edges(eng)}
    assert ids == {"E2"}
