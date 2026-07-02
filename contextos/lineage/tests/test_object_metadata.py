"""对象元数据拉取层测试。FakeQuerier 按 SQL 关键字返不同 ALL_* 行, 不碰真库。"""
from contextos.storage.db import make_engine


class FakeObjQuerier:
    """按 SQL 里出现的视图名返回对应 fake 行(大写列名, 对齐 Oracle 驱动)。"""
    def query(self, sql, params=None):
        s = sql.upper()
        if "ALL_TAB_COLUMNS" in s:
            return [{"OWNER": "UPC", "TABLE_NAME": "CB_CUSTOMER", "COLUMN_NAME": "CUST_ID",
                     "DATA_TYPE": "NUMBER", "NULLABLE": "N", "COMMENTS": "客户ID", "COLUMN_ID": 1}]
        if "ALL_INDEXES" in s:
            return [{"OWNER": "UPC", "INDEX_NAME": "IDX_CUST", "TABLE_NAME": "CB_CUSTOMER",
                     "UNIQUENESS": "UNIQUE", "COLUMN_LIST": "CUST_ID"}]
        if "ALL_CONSTRAINTS" in s:
            return [{"OWNER": "UPC", "CONSTRAINT_NAME": "PK_CUST", "TABLE_NAME": "CB_CUSTOMER",
                     "CONSTRAINT_TYPE": "P", "R_OWNER": None, "R_CONSTRAINT_NAME": None,
                     "SEARCH_CONDITION": None}]
        if "ALL_SEQUENCES" in s:
            return [{"SEQUENCE_OWNER": "UPC", "SEQUENCE_NAME": "SEQ_CUST", "MIN_VALUE": "1",
                     "MAX_VALUE": "9999999999", "INCREMENT_BY": "1", "LAST_NUMBER": "42",
                     "CACHE_SIZE": "20", "CYCLE_FLAG": "N"}]
        if "ALL_VIEWS" in s:
            return [{"OWNER": "UPC", "VIEW_NAME": "V_CUST"}]
        if "ALL_PROCEDURES" in s or "ALL_OBJECTS" in s:
            return [{"OWNER": "UPC", "OBJECT_NAME": "PKG_CUST", "OBJECT_TYPE": "PACKAGE"}]
        if "ALL_DEPENDENCIES" in s:
            return [{"OWNER": "UPC", "NAME": "V_CUST", "TYPE": "VIEW", "REFERENCED_OWNER": "UPC",
                     "REFERENCED_NAME": "CB_CUSTOMER", "REFERENCED_TYPE": "TABLE",
                     "REFERENCED_LINK_NAME": None}]
        return []


def test_refresh_object_metadata_loads_all_seven():
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata
    eng = make_engine("sqlite://")
    store.create_all(eng)
    out = refresh_object_metadata(FakeObjQuerier(), eng, owners=["UPC"], db_name="CCRM3",
                                  now="2026-06-06T00:00:00")
    assert out["refreshed"] is True
    assert store.all_columns(eng)[0]["column_name"] == "CUST_ID"
    assert store.all_sequences(eng)[0]["last_number"] == "42"
    assert store.all_dependencies(eng)[0]["referenced_name"] == "CB_CUSTOMER"
    assert store.all_procedures(eng)[0]["object_type"] == "PACKAGE"


def test_refresh_object_metadata_empty_owners_keeps_snapshot():
    """空 owners 不清快照(对齐 refresh_metadata 的 fail-safe)。"""
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata
    eng = make_engine("sqlite://")
    store.create_all(eng)
    store.write_views(eng, [dict(owner="UPC", view_name="V_OLD", comment="", db_name="CCRM3")])
    out = refresh_object_metadata(FakeObjQuerier(), eng, owners=[], db_name="CCRM3",
                                  now="2026-06-06T00:00:00")
    assert out["refreshed"] is False
    assert store.all_views(eng)[0]["view_name"] == "V_OLD"   # 旧快照保留


def test_refresh_object_metadata_owner_via_bind_not_literal():
    """owner 走 bind params, 非法 owner 被 _validate_owner 拒(纵深防御 K1)。"""
    import pytest
    from contextos.lineage.oracle_metadata import refresh_object_metadata
    from contextos.lineage import store
    eng = make_engine("sqlite://")
    store.create_all(eng)
    with pytest.raises(ValueError):
        refresh_object_metadata(FakeObjQuerier(), eng, owners=["UPC; DROP TABLE T"],
                                db_name="CCRM3", now="2026-06-06T00:00:00")


class DeadOracleQuerier:
    """模拟 Oracle 整库失联(断连/超时): 每个查询都抛 RuntimeError(ORA-12541 等)。"""
    def query(self, sql, params=None, **kw):
        raise RuntimeError("ORA-12541 TNS:no listener")


def test_refresh_object_metadata_keeps_snapshot_on_dead_oracle():
    """blocker 回归: 真 Oracle 整库失联(全查询抛)时, 必查门让 refresh 走 fail-safe ->
    保留旧快照不清空、不盖时间戳(对齐 refresh_metadata 的'拉失败绝不清空'承诺)。

    旧实现把全部 7 查询走 _safe_query 吞成 [] -> refreshed=True -> clear WIPE 好快照。
    本测试在旧实现下会失败(views 被清空), 正好暴露 blocker。"""
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata
    eng = make_engine("sqlite://")
    store.create_all(eng)
    # 先用好数据建一份成功快照
    refresh_object_metadata(FakeObjQuerier(), eng, owners=["UPC"], db_name="CCRM3",
                            now="2026-06-01T00:00:00")
    assert store.all_views(eng)[0]["view_name"] == "V_CUST"
    assert len(store.all_columns(eng)) == 1
    # Oracle 整库失联 -> 必查抛 -> 保留旧快照
    out = refresh_object_metadata(DeadOracleQuerier(), eng, owners=["UPC"], db_name="CCRM3",
                                  now="2026-06-03T00:00:00")
    assert out["refreshed"] is False                          # 没有误标刷新成功
    assert store.all_views(eng)[0]["view_name"] == "V_CUST"   # 旧快照仍在, 没被清空
    assert len(store.all_columns(eng)) == 1                   # 旧列也在
    assert store.get_meta(eng, "object_metadata_refreshed_at") == "2026-06-01T00:00:00"  # 时间戳没更新


class BadColumnIdQuerier(FakeObjQuerier):
    """驱动返回非数字 COLUMN_ID(如 'N/A'): int 强转会抛 ValueError。
    回归 issue #2: 这条 ValueError 不该被 'except ValueError: raise' 当 K1 注入错误冒泡崩整轮。"""
    def query(self, sql, params=None):
        if "ALL_TAB_COLUMNS" in sql.upper():
            return [{"OWNER": "UPC", "TABLE_NAME": "CB_CUSTOMER", "COLUMN_NAME": "CUST_ID",
                     "DATA_TYPE": "NUMBER", "NULLABLE": "N", "COMMENTS": "客户ID",
                     "COLUMN_ID": "N/A"}]
        return super().query(sql, params)


def test_refresh_object_metadata_bad_column_id_does_not_crash():
    """issue #2 回归: 驱动返回非数字 COLUMN_ID 时不抛 ValueError 冒泡崩刷新,
    _safe_int 兜底成 0, 该行仍正常写入(数据脏值不应当 K1 注入错误处理)。"""
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata
    eng = make_engine("sqlite://")
    store.create_all(eng)
    out = refresh_object_metadata(BadColumnIdQuerier(), eng, owners=["UPC"], db_name="CCRM3",
                                  now="2026-06-06T00:00:00")
    assert out["refreshed"] is True                           # 没崩, 正常刷新
    cols = store.all_columns(eng)
    assert cols[0]["column_name"] == "CUST_ID"
    assert cols[0]["column_id"] == 0                          # 脏值兜底成 0


def test_load_object_metadata_into_store_direct():
    """load_object_metadata_into_store 单 owner append 写库 + 计数(此前无直接测试)。"""
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import load_object_metadata_into_store
    eng = make_engine("sqlite://")
    store.create_all(eng)
    counts = load_object_metadata_into_store(FakeObjQuerier(), eng, owner="UPC", db_name="CCRM3")
    assert counts["columns"] == 1
    assert counts["views"] == 1
    assert counts["dependencies"] == 1
    assert store.all_columns(eng)[0]["column_name"] == "CUST_ID"
    assert store.all_dependencies(eng)[0]["referenced_name"] == "CB_CUSTOMER"


def test_fetch_object_metadata_includes_dblinks():
    """fetch_object_metadata 返回 dblinks 键; ALL_DB_LINKS 用 _safe_query 缺权限降级返空不阻塞。"""
    from contextos.lineage.oracle_metadata import fetch_object_metadata

    class DbLinkQuerier:
        def query(self, sql, params=None):
            if "ALL_DB_LINKS" in sql:
                return [{"OWNER": "UPC", "DB_LINK": "BILLING.WORLD", "HOST": "BILLINGDB",
                         "USERNAME": "RPT", "CREATED": "2020-01-01"}]
            if "ALL_TAB_COLUMNS" in sql:   # columns 是必查门, 不能空
                return [{"OWNER": "UPC", "TABLE_NAME": "T", "COLUMN_NAME": "C",
                         "DATA_TYPE": "VARCHAR2", "NULLABLE": "Y", "COLUMN_ID": 1, "COMMENTS": ""}]
            return []

    data = fetch_object_metadata(DbLinkQuerier(), owner="UPC", db_name="CCRM3")
    assert data["dblinks"] == [dict(owner="UPC", db_link="BILLING.WORLD", host="BILLINGDB",
                                    username="RPT", created="2020-01-01", db_name="CCRM3")]


def test_refresh_object_metadata_persists_dblinks():
    """refresh_object_metadata 把 dblinks 写入 store + clear 后正确重填。"""
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata
    from contextos.storage.db import make_engine

    class DbLinkQuerier:
        def query(self, sql, params=None):
            if "ALL_DB_LINKS" in sql:
                return [{"OWNER": "UPC", "DB_LINK": "L", "HOST": "H",
                         "USERNAME": "U", "CREATED": ""}]
            if "ALL_TAB_COLUMNS" in sql:
                return [{"OWNER": "UPC", "TABLE_NAME": "T", "COLUMN_NAME": "C",
                         "DATA_TYPE": "X", "NULLABLE": "Y", "COLUMN_ID": 1, "COMMENTS": ""}]
            return []

    e = make_engine("sqlite://"); store.create_all(e)
    refresh_object_metadata(DbLinkQuerier(), e, owners=["UPC"], db_name="CCRM3",
                            now="2026-06-06T00:00:00")
    assert len(store.all_dblinks(e)) == 1


def test_refresh_object_metadata_multi_owner_public_dblinks_no_duplicate():
    """回归: multi-owner 时 PUBLIC dblinks 被每个 owner 查询各返一次, 合并后重复
    (PK = owner/db_link)-> IntegrityError 崩整轮且旧快照已 clear。
    修法: 合并后按 (owner, db_link) 去重, 最终写库行数 = 唯一 PK 数。

    测试设计:
    - owners=['OWNER_A', 'OWNER_B'] 各触发一次 _Q_DBLINKS 查询。
    - 每次都返回同一条 PUBLIC dblink (OWNER='PUBLIC', DB_LINK='SHARED.WORLD')。
    - 不去重时 merged['dblinks'] 含 2 条相同 PK 行 -> IntegrityError。
    - 去重后只有 1 条 -> 写库成功; 私有 dblink 各 owner 1 条 -> 共 2+1=3 行。
    评分标准: refreshed=True(不崩) + all_dblinks 行数正确(私有 2 + 公共 1 = 3)。"""
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata
    from contextos.storage.db import make_engine

    class MultiOwnerPublicQuerier:
        """方案 B 批量 ALL_DB_LINKS 查询(OWNER IN (:o0,:o1) OR OWNER='PUBLIC'): 每 owner 私有 1 条
        + PUBLIC 共享 1 条(批量这里给两遍 PUBLIC, 触发 merged 重复 PK -> 验去重路径)。
        owner 从 o-bind 派生。"""
        def query(self, sql, params=None):
            owners = [v for k, v in (params or {}).items()
                      if k[:1] == "o" and k[1:].isdigit()] or ["OWNER_X"]
            if "ALL_DB_LINKS" in sql.upper():
                rows = [{"OWNER": o, "DB_LINK": f"PRIV_{o}.WORLD",   # 各 owner 私有(PK 不重叠)
                         "HOST": "PRIVATE_HOST", "USERNAME": "USR", "CREATED": ""} for o in owners]
                # PUBLIC 给两遍 -> merged 重复 PK -> 需去重保留一条
                rows += [{"OWNER": "PUBLIC", "DB_LINK": "SHARED.WORLD",
                          "HOST": "SHARED_HOST", "USERNAME": "PUB", "CREATED": ""}] * 2
                return rows
            if "ALL_TAB_COLUMNS" in sql.upper():
                return [{"OWNER": o, "TABLE_NAME": "T", "COLUMN_NAME": "C",
                         "DATA_TYPE": "VARCHAR2", "NULLABLE": "Y", "COLUMN_ID": 1,
                         "COMMENTS": ""} for o in owners]
            return []

    e = make_engine("sqlite://")
    store.create_all(e)
    out = refresh_object_metadata(
        MultiOwnerPublicQuerier(), e,
        owners=["OWNER_A", "OWNER_B"], db_name="CCRM3",
        now="2026-06-06T00:00:00",
    )
    assert out["refreshed"] is True, f"refreshed 应为 True, 得到: {out}"
    rows = store.all_dblinks(e)
    # OWNER_A 私有 1 + OWNER_B 私有 1 + PUBLIC 共享 1(去重后) = 3
    assert len(rows) == 3, f"期望 3 行 dblinks, 得到 {len(rows)}: {rows}"
    db_link_names = {r["db_link"] for r in rows}
    assert "SHARED.WORLD" in db_link_names
    assert "PRIV_OWNER_A.WORLD" in db_link_names
    assert "PRIV_OWNER_B.WORLD" in db_link_names


# --- option A: 数据库维度默认只抓表级血缘需要的对象元数据(dependencies + dblinks),
#     跳过 columns/indexes/constraints(per-table 重查, 某大型客户代码库满库 ~40min 墙, 唯一消费方是
#     config 维度)+ sequences/views/procedures(全仓无消费方)。scope="full" 留给将来 config
#     按 LP 模板归并抓列时 opt-in。canary 必查门从 columns 换成 dependencies。---

class _FullFakeQuerier(FakeObjQuerier):
    """在 FakeObjQuerier(返 columns/deps/...)基础上补 dblinks 一行。"""
    def query(self, sql, params=None):
        if "ALL_DB_LINKS" in sql.upper():
            return [{"OWNER": "UPC", "DB_LINK": "LNK1", "HOST": "h",
                     "USERNAME": "u", "CREATED": None}]
        return super().query(sql, params)


def test_lineage_scope_fetches_only_deps_and_dblinks():
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata
    eng = make_engine("sqlite://"); store.create_all(eng)
    out = refresh_object_metadata(_FullFakeQuerier(), eng, owners=["UPC"], db_name="CCRM3",
                                  now="2026-06-06T00:00:00", scope="lineage")
    assert out["refreshed"] is True
    # 表级血缘需要的两类: 抓到
    assert store.all_dependencies(eng)[0]["referenced_name"] == "CB_CUSTOMER"
    assert store.all_dblinks(eng)[0]["db_link"] == "LNK1"
    # config-only / 无消费方的: lineage scope 不抓
    assert store.all_columns(eng) == []
    assert store.all_indexes(eng) == []
    assert store.all_constraints(eng) == []
    assert store.all_sequences(eng) == []
    assert store.all_views(eng) == []
    assert store.all_procedures(eng) == []


def test_lineage_scope_canary_is_dependencies_columns_never_queried():
    """lineage scope 下 columns 查询根本不发出: 即便 querier 在 columns 上抛, 也不该影响刷新
    (canary 已换 dependencies)。"""
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata
    eng = make_engine("sqlite://"); store.create_all(eng)

    class _ColBoomDepsOk:
        def query(self, sql, params=None):
            s = sql.upper()
            if "ALL_TAB_COLUMNS" in s:
                raise RuntimeError("ORA-12541 columns 不该被查到")
            if "ALL_DEPENDENCIES" in s:
                return [{"OWNER": "UPC", "NAME": "V_NEW", "TYPE": "VIEW",
                         "REFERENCED_OWNER": "UPC", "REFERENCED_NAME": "T_NEW",
                         "REFERENCED_TYPE": "TABLE", "REFERENCED_LINK_NAME": None}]
            return []

    out = refresh_object_metadata(_ColBoomDepsOk(), eng, owners=["UPC"], db_name="CCRM3",
                                  now="2026-06-07T00:00:00", scope="lineage")
    assert out["refreshed"] is True                                   # 没被 columns 抛波及
    assert store.all_dependencies(eng)[0]["referenced_name"] == "T_NEW"


def test_lineage_scope_dead_dependencies_preserves_snapshot():
    """断连(dependencies 查询抛)-> 不清空旧对象快照(canary fail-safe 仍生效)。"""
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata
    eng = make_engine("sqlite://"); store.create_all(eng)
    store.write_dependencies(eng, [dict(owner="UPC", name="V_OLD", type="VIEW",
        referenced_owner="UPC", referenced_name="T_OLD", referenced_type="TABLE",
        referenced_link_name="", db_name="CCRM3")])

    class _DepsDead:
        def query(self, sql, params=None):
            if "ALL_DEPENDENCIES" in sql.upper():
                raise RuntimeError("ORA-12537 connection closed")
            return []

    out = refresh_object_metadata(_DepsDead(), eng, owners=["UPC"], db_name="CCRM3",
                                  now="2026-06-07T00:00:00", scope="lineage")
    assert out["refreshed"] is False
    assert store.all_dependencies(eng)[0]["referenced_name"] == "T_OLD"   # 旧快照保留


def test_lineage_scope_dead_dblinks_preserves_snapshot():
    """断连发生在 dependencies canary 成功之后、dblinks 抓取期间: dblinks 也必须当 canary
    (_fetch_all_meta 失败抛), 否则 _safe_query 吞异常 -> 写空 dblinks -> WIPE 旧快照而谎报成功
    (review blocker)。lineage scope 只抓 deps+dblinks 两类, 两个都得是必查门。"""
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata
    eng = make_engine("sqlite://"); store.create_all(eng)
    store.write_dblinks(eng, [dict(owner="UPC", db_link="LNK_OLD", host="h",
                                   username="u", created="", db_name="CCRM3")])

    class _DepsOkDblinksDead:
        def query(self, sql, params=None):
            s = sql.upper()
            if "ALL_DEPENDENCIES" in s:
                return [{"OWNER": "UPC", "NAME": "V", "TYPE": "VIEW", "REFERENCED_OWNER": "UPC",
                         "REFERENCED_NAME": "T", "REFERENCED_TYPE": "TABLE",
                         "REFERENCED_LINK_NAME": None}]
            if "ALL_DB_LINKS" in s:
                raise RuntimeError("ORA-12537 connection closed mid-fetch")
            return []

    out = refresh_object_metadata(_DepsOkDblinksDead(), eng, owners=["UPC"], db_name="CCRM3",
                                  now="2026-06-07T00:00:00", scope="lineage")
    assert out["refreshed"] is False                                  # 不谎报成功
    assert store.all_dblinks(eng)[0]["db_link"] == "LNK_OLD"          # 旧 dblinks 快照保留, 没被 WIPE
