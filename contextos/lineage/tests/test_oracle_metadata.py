"""Oracle 元数据层纯逻辑测试。FakeQuerier 喂 canned 行, 不连真库。"""
import pytest

from contextos.db_provider.oracle_gate import OracleSafetyError
from contextos.storage.db import make_engine
from contextos.lineage import store


class FakeQuerier:
    """模拟一个能跑只读 SQL 的连接: 按 SQL 关键词返回 canned 行。"""
    def __init__(self):
        self.calls = []

    def query(self, sql, params=None, **kw):
        self.calls.append(sql)
        upper = sql.upper()
        if "ALL_TAB_COMMENTS" in upper:
            return [{"OWNER": "UPC", "TABLE_NAME": "PM_OFFER_CHA",
                     "TABLE_TYPE": "TABLE", "COMMENTS": "Offer 渠道授权表"},
                    {"OWNER": "UPC", "TABLE_NAME": "V_OFFER",
                     "TABLE_TYPE": "VIEW", "COMMENTS": ""}]
        if "ALL_SYNONYMS" in upper or "DBA_SYNONYMS" in upper:
            return [{"SYNONYM_NAME": "SYN_OFFER", "TABLE_OWNER": "UPC",
                     "TABLE_NAME": "PM_OFFER_CHA", "DB_LINK": None}]
        if "CONSTRAINTS" in upper:
            return [{"TABLE_NAME": "PM_OFFER_CHA", "FK_REF_TABLE": "PM_OFFER_BASE"}]
        return []


def test_execute_query_wraps_rownum_and_gates():
    from contextos.lineage.oracle_metadata import execute_query
    q = FakeQuerier()
    # 只读查询通过 + ROWNUM 包装
    rows = execute_query(q, "SELECT * FROM ALL_TAB_COMMENTS WHERE OWNER='UPC'", max_rows=10)
    assert q.calls[0].upper().startswith("SELECT * FROM (")
    assert "ROWNUM" in q.calls[0].upper()
    # 写 SQL 被 oracle_gate 硬拒
    with pytest.raises(OracleSafetyError):
        execute_query(q, "DELETE FROM T", max_rows=10)


def test_metadata_full_snapshot_not_truncated_at_1000():
    """元数据全量快照不被 1000 行硬上限截断(review Finding #2)。

    owner 有 1001 张表 -> 全收, 不静默截成 1000(部分快照 + 在线 validate 会丢双 unknown 边)。"""
    from contextos.lineage.oracle_metadata import fetch_metadata

    class BigQuerier:
        def query(self, sql, params=None, **kw):
            if "ALL_TAB_COMMENTS" in sql.upper():
                return [{"OWNER": "UPC", "TABLE_NAME": f"T{i}", "TABLE_TYPE": "TABLE",
                         "COMMENTS": ""} for i in range(1001)]
            return []

    data = fetch_metadata(BigQuerier(), owner="UPC")
    assert len(data["tables"]) == 1001


def test_fetch_all_meta_warns_at_safety_ceiling(monkeypatch, caplog):
    """全量拉取撞安全上限 -> 告警(reviewer Minor #1: 别再静默截断, 即便上限很高)。"""
    import contextos.lineage.oracle_metadata as om
    monkeypatch.setattr(om, "_META_MAX_ROWS", 2)

    class Ceil:
        def query(self, sql, params=None, **kw):
            return [{"X": 1}, {"X": 2}]      # 恰好等于上限 -> 疑似被截断

    with caplog.at_level("WARNING"):
        om._fetch_all_meta(Ceil(), "SELECT 1 FROM DUAL")
    assert "ceiling" in caplog.text.lower()


def test_owner_rejects_sql_injection():
    """owner 走 identifier 校验, 不字符串拼 SQL: 注入式 owner 被拒(不扩 SELECT)。

    回归 review Finding #4: 原 .replace(':owner', f\"'{owner}'\") 字符串插值,
    owner=\"UPC' OR '1'='1\" 能扩 broadened SELECT(只读 gate 挡写不挡条件扩展)。"""
    from contextos.lineage.oracle_metadata import fetch_metadata
    q = FakeQuerier()
    with pytest.raises(ValueError):
        fetch_metadata(q, owner="UPC' OR '1'='1")


def test_fetch_metadata_binds_owner_not_interpolated():
    """合法 owner 通过 params 绑定, 不内联进 SQL 文本(:owner 占位符保留)。"""
    from contextos.lineage.oracle_metadata import fetch_metadata
    calls = []

    class Spy:
        def query(self, sql, params=None, **kw):
            calls.append((sql, params))
            if "ALL_TAB_COMMENTS" in sql.upper():
                return [{"OWNER": "UPC", "TABLE_NAME": "T1", "TABLE_TYPE": "TABLE",
                         "COMMENTS": ""}]
            return []

    fetch_metadata(Spy(), owner="UPC")
    sql, params = [c for c in calls if "ALL_TAB_COMMENTS" in c[0].upper()][0]
    # 方案 B 批量后: owner 走 IN (:o0) bind(单 owner = 一元素列表), 仍是 bind 不内联。
    assert ":o0" in sql and "IN(" in sql.replace(" ", "")  # 占位符 + IN 子句在(去空格后)
    assert "'UPC'" not in sql                # owner 没内联进 SQL
    assert params == {"o0": "UPC"}


def test_load_table_metadata_into_store():
    from contextos.lineage.oracle_metadata import load_metadata_into_store
    eng = make_engine("sqlite://")
    store.create_all(eng)
    q = FakeQuerier()
    summary = load_metadata_into_store(q, eng, owner="UPC", db_name="CCRM3")
    md = {r["template_name"]: r for r in store.all_table_metadata(eng)}
    assert md["PM_OFFER_CHA"]["comment"] == "Offer 渠道授权表"
    assert md["PM_OFFER_CHA"]["db_name"] == "CCRM3"
    assert md["V_OFFER"]["dataset_type"] == "VIEW"
    syn = store.all_synonyms(eng)
    assert syn[0]["synonym_name"] == "SYN_OFFER"
    fks = store.all_fks(eng)
    assert any(f["table_a"] == "PM_OFFER_CHA" and f["table_b"] == "PM_OFFER_BASE" for f in fks)
    assert summary["tables"] == 2 and summary["synonyms"] == 1 and summary["fks"] == 1


# ---------------------------------------------------------------------------
# 方案 B: 表名排除(exclude_table_patterns)—— 削减某大型客户代码库海量历史/分区表元数据抓取
# ---------------------------------------------------------------------------


def test_with_table_exclusions_empty_is_noop():
    from contextos.lineage.oracle_metadata import _with_table_exclusions
    sql, params = _with_table_exclusions("SELECT 1 FROM T", [])
    assert sql == "SELECT 1 FROM T"
    assert params == {}


def test_with_table_exclusions_wraps_and_binds():
    from contextos.lineage.oracle_metadata import _with_table_exclusions
    base = "SELECT OWNER, TABLE_NAME FROM ALL_TAB_COLUMNS WHERE OWNER=:owner"
    sql, params = _with_table_exclusions(base, [r"_[0-9]{6}$", r"_BAK$"])
    up = sql.upper()
    assert up.startswith("SELECT * FROM (")           # 原查询被包成子查询
    assert "ALL_TAB_COLUMNS" in sql                   # 原查询保留
    assert up.count("NOT REGEXP_LIKE(TABLE_NAME") == 2
    assert params == {"ex0": r"_[0-9]{6}$", "ex1": r"_BAK$"}   # 正则走 bind, 不内联


class _CapQuerier:
    """捕获每次下发的 (SQL_upper, params), 按视图名 canned 返回。"""
    def __init__(self):
        self.calls = []

    def query(self, sql, params=None, **kw):
        self.calls.append((sql.upper(), dict(params or {})))
        up = sql.upper()
        if "ALL_TAB_COMMENTS" in up:
            return [{"OWNER": "OWX", "TABLE_NAME": "TB", "TABLE_TYPE": "TABLE", "COMMENTS": ""}]
        if "ALL_TAB_COLUMNS" in up:
            return [{"OWNER": "OWX", "TABLE_NAME": "TB", "COLUMN_NAME": "C",
                     "DATA_TYPE": "VARCHAR2", "NULLABLE": "Y", "COLUMN_ID": 1, "COMMENTS": ""}]
        return []

    def by_view(self):
        out = {}
        for sql, params in self.calls:
            if "ALL_TAB_COMMENTS" in sql:        out["tab"] = (sql, params)
            elif "ALL_TAB_COLUMNS" in sql:       out["col"] = (sql, params)
            elif "ALL_SYNONYMS" in sql:          out["syn"] = (sql, params)
            elif "ALL_INDEXES" in sql:           out["idx"] = (sql, params)
            elif "ALL_CONSTRAINTS" in sql and "R_CONSTRAINT_NAME = R" in sql.replace(" ", " "):
                out.setdefault("fk", (sql, params))
            elif "ALL_CONSTRAINTS" in sql:       out.setdefault("con", (sql, params))
            elif "ALL_SEQUENCES" in sql:         out["seq"] = (sql, params)
            elif "ALL_VIEWS" in sql:             out["view"] = (sql, params)
            elif "ALL_OBJECTS" in sql:           out["proc"] = (sql, params)
            elif "ALL_DEPENDENCIES" in sql:      out["dep"] = (sql, params)
            elif "ALL_DB_LINKS" in sql:          out["dbl"] = (sql, params)
        return out


def test_fetch_metadata_applies_exclusions_to_table_queries_only():
    from contextos.lineage.oracle_metadata import fetch_metadata
    q = _CapQuerier()
    fetch_metadata(q, owner="OWX", exclude_table_patterns=[r"_[0-9]{6}$"])
    v = q.by_view()
    # tab_comments + fks 带排除
    assert "NOT REGEXP_LIKE(TABLE_NAME" in v["tab"][0] and v["tab"][1].get("ex0") == r"_[0-9]{6}$"
    assert "NOT REGEXP_LIKE(TABLE_NAME" in v["fk"][0]
    # synonyms 不排除(虽有 TABLE_NAME 列, 设计上不过滤同义词)
    assert "NOT REGEXP_LIKE" not in v["syn"][0]


def test_fetch_metadata_default_no_exclusions():
    from contextos.lineage.oracle_metadata import fetch_metadata
    q = _CapQuerier()
    fetch_metadata(q, owner="OWX")           # 默认不传 -> 零行为变更
    assert all("NOT REGEXP_LIKE" not in sql for sql, _ in q.calls)


def test_fetch_object_metadata_excludes_table_queries_not_others():
    from contextos.lineage.oracle_metadata import fetch_object_metadata
    q = _CapQuerier()
    fetch_object_metadata(q, owner="OWX", exclude_table_patterns=[r"_[0-9]{6}$"])
    v = q.by_view()
    # 有 TABLE_NAME 的 table 类(实际会跑的): columns / constraints / indexes 带排除
    assert "NOT REGEXP_LIKE(TABLE_NAME" in v["col"][0]
    assert "NOT REGEXP_LIKE(TABLE_NAME" in v["con"][0]
    # indexes(_Q_INDEXES)含 'LISTAGG ... ON OVERFLOW TRUNCATE': 闸门放宽后通过(只拦
    # TRUNCATE TABLE/CLUSTER), 该查询真正下发并带表名排除(见 test_index_query_passes_gate)。
    assert "NOT REGEXP_LIKE(TABLE_NAME" in v["idx"][0]
    # 非 table 键的不排除: sequences / views / procedures / dependencies / dblinks
    for k in ("seq", "view", "proc", "dep", "dbl"):
        assert "NOT REGEXP_LIKE" not in v[k][0], f"{k} 不应带表名排除"


def test_index_query_passes_gate():
    """_Q_INDEXES 含 LISTAGG ... ON OVERFLOW TRUNCATE(溢出子句关键字, 非 TRUNCATE TABLE
    写语句), 必须通过只读闸门 —— 否则 _safe_query 把它吞成空, indexes 元数据从不下发。

    回归: 闸门曾按裸 \\bTRUNCATE\\b 误判此查询为写操作(独立于方案 B 的 pre-existing 缺陷);
    放宽为只拦 TRUNCATE TABLE / CLUSTER 后此查询应放行。"""
    from contextos.db_provider.oracle_gate import assert_query_is_readonly
    from contextos.lineage.oracle_metadata import _Q_INDEXES
    assert_query_is_readonly(_Q_INDEXES)        # 不应抛 OracleSafetyError


def test_fetch_object_metadata_returns_indexes_not_swallowed():
    """端到端: querier 给出索引行时, fetch_object_metadata 的 indexes 应真正下发并填充。

    修闸门前: _Q_INDEXES 被误判 -> _safe_query 吞成 [] -> indexes 永远为空(即便库里有)。
    本测试用返回真索引行的 querier 证明该路径已通(列映射 + LISTAGG 列清单原样保留)。"""
    from contextos.lineage.oracle_metadata import fetch_object_metadata

    class IdxQuerier:
        def query(self, sql, params=None, **kw):
            if "ALL_INDEXES" in sql.upper():
                return [{"OWNER": "OWX", "INDEX_NAME": "IDX_TB_C", "TABLE_NAME": "TB",
                         "UNIQUENESS": "NONUNIQUE", "COLUMN_LIST": "C1,C2"}]
            if "ALL_TAB_COLUMNS" in sql.upper():     # columns 是必查门, 给一行避免抛
                return [{"OWNER": "OWX", "TABLE_NAME": "TB", "COLUMN_NAME": "C1",
                         "DATA_TYPE": "VARCHAR2", "NULLABLE": "Y", "COLUMN_ID": 1, "COMMENTS": ""}]
            return []

    data = fetch_object_metadata(IdxQuerier(), owner="OWX")
    assert len(data["indexes"]) == 1
    idx = data["indexes"][0]
    assert idx["index_name"] == "IDX_TB_C"
    assert idx["table_name"] == "TB"
    assert idx["column_list"] == "C1,C2"


def test_refresh_multi_threads_exclusions_to_fetch():
    """方案 B: refresh_metadata_multi / refresh_object_metadata_multi 把 exclude_table_patterns
    透传到底层 fetch -> 下发的 table 类查询带 NOT REGEXP_LIKE。"""
    from dataclasses import dataclass

    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import (
        refresh_metadata_multi, refresh_object_metadata_multi,
    )
    from contextos.storage.db import make_engine

    @dataclass
    class _Spec:
        tns: str
        db_name: str
        owners: list

    specs = [_Spec(tns="T1", db_name="D1", owners=["OWX"])]
    now = "2026-06-07T00:00:00"

    eng = make_engine("sqlite://")
    store.create_all(eng)
    q = _CapQuerier()
    refresh_metadata_multi(eng, specs, querier_factory=lambda tns: q, now=now,
                           exclude_table_patterns=[r"_[0-9]{6}$"])
    assert any("NOT REGEXP_LIKE(TABLE_NAME" in sql for sql, _ in q.calls)

    eng2 = make_engine("sqlite://")
    store.create_all(eng2)
    q2 = _CapQuerier()
    refresh_object_metadata_multi(eng2, specs, querier_factory=lambda tns: q2, now=now,
                                  exclude_table_patterns=[r"_[0-9]{6}$"])
    assert any("NOT REGEXP_LIKE(TABLE_NAME" in sql for sql, _ in q2.calls)


def test_refresh_metadata_multi_is_bulk_one_query_per_type():
    """方案 B: N 个 owner 应一条 OWNER IN (...) 批量查(不是 N 条逐 owner), 结果按行 OWNER 分组。"""
    from dataclasses import dataclass

    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_metadata_multi
    from contextos.storage.db import make_engine

    @dataclass
    class _Spec:
        tns: str
        db_name: str
        owners: list

    eng = make_engine("sqlite://")
    store.create_all(eng)
    calls = []

    class Q:
        def query(self, sql, params=None, **kw):
            calls.append(sql.upper())
            if "ALL_TAB_COMMENTS" in sql.upper():     # 批量返回多 owner 行
                return [{"OWNER": "OWA", "TABLE_NAME": "T1", "TABLE_TYPE": "TABLE", "COMMENTS": ""},
                        {"OWNER": "OWB", "TABLE_NAME": "T2", "TABLE_TYPE": "TABLE", "COMMENTS": ""}]
            return []

    q = Q()
    spec = _Spec(tns="T1", db_name="D1", owners=["OWA", "OWB", "OWC"])
    refresh_metadata_multi(eng, [spec], querier_factory=lambda t: q, now="2026-06-07T00:00:00")

    tabq = [s for s in calls if "ALL_TAB_COMMENTS" in s]
    assert len(tabq) == 1, f"3 owner 应 1 条批量查, 实际 {len(tabq)} 条"
    assert "IN (" in tabq[0]                       # OWNER IN (...)
    tables = {r["template_name"] for r in store.all_table_metadata(eng)}
    assert tables == {"T1", "T2"}                  # 按行 OWNER 分组, 两 owner 的表都进库


def test_refresh_object_metadata_multi_is_bulk_one_query_per_type():
    """方案 B: 对象元数据 columns 也批量(N owner -> 1 条 ALL_TAB_COLUMNS 查)。"""
    from dataclasses import dataclass

    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata_multi
    from contextos.storage.db import make_engine

    @dataclass
    class _Spec:
        tns: str
        db_name: str
        owners: list

    eng = make_engine("sqlite://")
    store.create_all(eng)
    calls = []

    class Q:
        def query(self, sql, params=None, **kw):
            calls.append(sql.upper())
            if "ALL_TAB_COLUMNS" in sql.upper():
                return [{"OWNER": "OWA", "TABLE_NAME": "T1", "COLUMN_NAME": "C",
                         "DATA_TYPE": "VARCHAR2", "NULLABLE": "Y", "COLUMN_ID": 1, "COMMENTS": ""}]
            return []

    q = Q()
    spec = _Spec(tns="T1", db_name="D1", owners=["OWA", "OWB", "OWC"])
    refresh_object_metadata_multi(eng, [spec], querier_factory=lambda t: q, now="2026-06-07T00:00:00")
    colq = [s for s in calls if "ALL_TAB_COLUMNS" in s]
    assert len(colq) == 1, f"3 owner 应 1 条批量 columns 查, 实际 {len(colq)} 条"
    assert "IN (" in colq[0]
