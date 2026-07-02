"""05 维证据 tool 函数纯逻辑测试(Plan 10 Task 4 / Block 1b Task 13 迁移)。

设计思路
--------
tools.py 是 MCP/CLI 共用的"查已 build 的血缘表 + (有 router 时)Oracle 元数据"薄层,
全返回纯 dict/list,不碰 MCP 协议。本测试覆盖两类路径:

1. 离线降级(router=None): 每个查 Oracle 的函数必须返回结构完整的 dict(本地血缘部分
   + note 标记),绝不抛。search_sql 纯本地无 Oracle。
2. 在线参数化(FakeQuerier 包在 _SingleRouter 里 + monkeypatch execute_query):
   断言 Oracle SQL 用了 :owner/:tbl/:name bind params(:tbl 避开 Oracle 保留字 TABLE;
   不把字符串拼进 SQL 文本,防注入面)。

Block 1b Task 13: querier= 参数已改名 router=。既有在线分支测试用 _SingleRouter 包住单
FakeQuerier 来模拟"只有一库的 router"(fan_out 返 [q]),保持语义等价 + 不破既有覆盖。

评分标准
--------
- 5 函数各自的离线分支返回正确 schema 且不抛。
- 查 Oracle 的 4 函数在线分支: 取回 Oracle 行 + SQL 全参数化(断言 ":owner" 在 SQL、owner
  值不内联进 SQL 文本、params 字典含绑定)。
- search_sql 字面命中 + 无命中返 []。

测试 fixture 用中性合成名(APP.ORDERS / ORDER_ITEMS / feature.flag.x),不掺真客户
schema/owner/表名(守 feedback_offline_test_neutral_fixtures)。

自动脚本逻辑
------------
内存 SQLite + store.create_all 建 schema,_seed 灌中性血缘/SQL 模板行。FakeQuerier 模拟
一个已连接的只读 Oracle 客户端: 按 SQL 关键词返回 canned 行(Oracle 列名大写约定),
记录每次 (sql, params) 供断言参数化。_SingleRouter 把 FakeQuerier 包成最小 router
(fan_out 返 [q], owner 路由返 None -> 退化到 fan-out)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine

from contextos.lineage import store, tools


# --------------------------------------------------------------------------- fixtures


def _seed(engine) -> None:
    """中性合成血缘/SQL 模板。表名一律 APP.* 合成名,不含真客户 schema。"""
    store.create_all(engine)
    with engine.begin() as c:
        c.execute(store.lineage_edges.insert(), [
            {"edge_id": "e1", "src_owner": "APP", "src_table": "ORDERS",
             "dst_owner": "APP", "dst_table": "ORDER_ITEMS",
             "relation_type": "JOIN", "confidence": "high", "evidence_count": 2},
            {"edge_id": "e2", "src_owner": "APP", "src_table": "CUSTOMERS",
             "dst_owner": "APP", "dst_table": "ORDERS",
             "relation_type": "WRITE", "confidence": "medium", "evidence_count": 1},
        ])
        c.execute(store.sql_templates.insert(), [
            {"template_id": "t1", "source_file": "X.java", "container": "XSvc.run",
             "sql_text": "SELECT * FROM ORDERS WHERE ID=?",
             "recovery_mode": "sql_file", "confidence": "high"},
            {"template_id": "t2", "source_file": "Y.java", "container": "YSvc.run",
             "sql_text": "INSERT INTO ORDER_ITEMS (ID) VALUES (?)",
             "recovery_mode": "mybatis", "confidence": "medium"},
        ])


class FakeQuerier:
    """模拟一个已连接的只读 Oracle 客户端: 按 SQL 关键词返回 canned 行(列名大写)。

    记录每次 (sql, params) 供断言: SQL 用 bind 占位符、owner/table/name 不内联。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def query(self, sql: str, params: dict[str, Any] | None = None,
              **_kw: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, params))
        upper = sql.upper()
        if "ALL_TAB_COLUMNS" in upper:
            return [{"COLUMN_NAME": "ID", "DATA_TYPE": "NUMBER"},
                    {"COLUMN_NAME": "NAME", "DATA_TYPE": "VARCHAR2"}]
        if "ALL_TAB_COMMENTS" in upper:
            return [{"COMMENTS": "order header table"}]
        if "DBA_DEPENDENCIES" in upper or "ALL_DEPENDENCIES" in upper:
            return [{"OWNER": "APP", "NAME": "V_ORDERS", "TYPE": "VIEW",
                     "REFERENCED_NAME": "ORDERS"}]
        if "ALL_SYNONYMS" in upper:
            return [{"SYNONYM_NAME": "SYN_ORDERS", "TABLE_OWNER": "APP",
                     "TABLE_NAME": "ORDERS"}]
        if "ALL_SEQUENCES" in upper:
            return [{"SEQUENCE_OWNER": "APP", "SEQUENCE_NAME": "ORDER_SEQ",
                     "MIN_VALUE": 1, "MAX_VALUE": 999, "INCREMENT_BY": 1}]
        return []


class _SingleRouter:
    """把单个 fake querier 当成只有一库的 router(迁移既有 tools 测试用)。

    fan_out 返 [q](等价旧行为: querier 在时直接用);owner 路由返 None -> 退化 fan-out。
    calls 属性透传到内部 querier,方便既有 assert 继续直接访问 q.calls。
    """

    def __init__(self, q: Any) -> None:
        self._q = q

    def resolve_owner_for_table(self, table: str) -> None:
        return None

    def querier_for_owner(self, owner: str) -> None:
        return None

    def fan_out(self) -> list[Any]:
        return [self._q] if self._q is not None else []


# --------------------------------------------------------------------------- search_sql


def test_search_sql_literal_match():
    e = create_engine("sqlite://")
    _seed(e)
    hits = tools.search_sql(e, pattern="FROM ORDERS")
    assert hits and hits[0]["template_id"] == "t1"
    assert hits[0]["source_file"] == "X.java"
    assert hits[0]["recovery_mode"] == "sql_file"
    assert "snippet" in hits[0]


def test_search_sql_no_match_returns_empty():
    e = create_engine("sqlite://")
    _seed(e)
    assert tools.search_sql(e, pattern="NONEXISTENT_XYZ") == []


def test_search_sql_limit_respected():
    e = create_engine("sqlite://")
    _seed(e)
    # both templates contain "ORDER" -> cap to 1
    hits = tools.search_sql(e, pattern="ORDER", limit=1)
    assert len(hits) == 1


def test_search_sql_empty_pattern_returns_empty():
    e = create_engine("sqlite://")
    _seed(e)
    assert tools.search_sql(e, pattern="") == []


# --------------------------------------------------------------------------- lookup_table


def test_lookup_table_offline_returns_local_lineage():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.lookup_table(e, table="ORDERS")           # querier=None
    assert r["table"] == "ORDERS"
    assert r["edges_out"] >= 1                            # ORDERS is src of e1
    assert r["edges_in"] >= 1                             # ORDERS is dst of e2
    assert r["columns"] == []                             # no Oracle -> empty
    assert r["note"] == "oracle_offline"


def test_lookup_table_online_parametrized():
    e = create_engine("sqlite://")
    _seed(e)
    q = FakeQuerier()
    r = tools.lookup_table(e, table="ORDERS", owner="APP", router=_SingleRouter(q))
    assert [c["column_name"] for c in r["columns"]] == ["ID", "NAME"]
    assert r["comment"] == "order header table"
    # 参数化断言: SQL 用 :owner / :tbl bind(:tbl 避开 Oracle 保留字 TABLE),owner/table 不内联
    col_sql, col_params = [c for c in q.calls if "ALL_TAB_COLUMNS" in c[0].upper()][0]
    assert ":owner" in col_sql and ":tbl" in col_sql
    assert "'APP'" not in col_sql and "'ORDERS'" not in col_sql
    assert col_params == {"owner": "APP", "tbl": "ORDERS"}


# --------------------------------------------------------------------------- lookup_lineage


def test_lookup_lineage_offline_local_only():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.lookup_lineage(e, table="ORDERS")          # querier=None
    # downstream = edges where ORDERS is src (-> ORDER_ITEMS via e1)
    assert any(d["table"] == "ORDER_ITEMS" for d in r["downstream"])
    # upstream = edges where ORDERS is dst (CUSTOMERS -> ORDERS via e2)
    assert any(u["table"] == "CUSTOMERS" for u in r["upstream"])
    assert r["note"] == "oracle_offline"


def test_lookup_lineage_online_parametrized():
    e = create_engine("sqlite://")
    _seed(e)
    q = FakeQuerier()
    r = tools.lookup_lineage(e, table="ORDERS", router=_SingleRouter(q))
    # Oracle DBA_DEPENDENCIES / ALL_SYNONYMS rows merged in
    dep_sql, dep_params = [c for c in q.calls
                           if "DEPENDENCIES" in c[0].upper()][0]
    assert ":tbl" in dep_sql
    assert "'ORDERS'" not in dep_sql
    assert dep_params is not None and dep_params.get("tbl") == "ORDERS"
    assert "note" not in r or r["note"] != "oracle_offline"


# --------------------------------------------------------------------------- lookup_dependency


def test_lookup_dependency_offline_degrades():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.lookup_dependency(e, name="V_ORDERS")      # querier=None
    assert r["name"] == "V_ORDERS"
    assert r["dependents"] == []
    assert r["note"] == "oracle_offline"


def test_lookup_dependency_online_parametrized():
    e = create_engine("sqlite://")
    _seed(e)
    q = FakeQuerier()
    r = tools.lookup_dependency(e, name="ORDERS", router=_SingleRouter(q))
    assert r["dependents"]                                # canned dependency row
    dep_sql, dep_params = [c for c in q.calls
                           if "DEPENDENCIES" in c[0].upper()][0]
    assert ":name" in dep_sql
    assert "'ORDERS'" not in dep_sql
    assert dep_params == {"name": "ORDERS"}


# --------------------------------------------------------------------------- lookup_sequence


def test_lookup_sequence_offline_degrades():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.lookup_sequence(e, name="ORDER_SEQ")       # querier=None
    assert r["name"] == "ORDER_SEQ"
    assert r["sequence"] is None
    assert r["note"] == "oracle_offline"


def test_lookup_sequence_online_parametrized():
    e = create_engine("sqlite://")
    _seed(e)
    q = FakeQuerier()
    r = tools.lookup_sequence(e, name="ORDER_SEQ", router=_SingleRouter(q))
    assert r["sequence"] is not None
    assert r["sequence"]["sequence_name"] == "ORDER_SEQ"
    seq_sql, seq_params = [c for c in q.calls
                           if "ALL_SEQUENCES" in c[0].upper()][0]
    assert ":name" in seq_sql
    assert "'ORDER_SEQ'" not in seq_sql
    assert seq_params == {"name": "ORDER_SEQ"}


# --------------------------------------------------------------------------- input guards


def test_offline_functions_reject_semicolon_in_name():
    """name/table 带分号(SQL 片段)-> 拒,纵深防御即便已参数化。"""
    e = create_engine("sqlite://")
    _seed(e)
    import pytest
    for call in (
        lambda: tools.lookup_table(e, table="ORDERS; DROP TABLE X"),
        lambda: tools.lookup_lineage(e, table="ORDERS; --"),
        lambda: tools.lookup_dependency(e, name="V; DELETE"),
        lambda: tools.lookup_sequence(e, name="S;"),
    ):
        with pytest.raises(ValueError):
            call()


def test_lookup_table_empty_table_rejected():
    e = create_engine("sqlite://")
    _seed(e)
    import pytest
    with pytest.raises(ValueError):
        tools.lookup_table(e, table="")


# ---------------------------------------------------------------- sequence capacity (Block 1a Task 7)


class _SeqQuerier:
    def query(self, sql, params=None):
        s = sql.upper()
        if "ALL_SEQUENCES" in s:
            # last=8500, min=1, max=10000 -> usage=85% -> 告警
            return [{"SEQUENCE_OWNER": "APP", "SEQUENCE_NAME": "ORDER_SEQ", "MIN_VALUE": "1",
                     "MAX_VALUE": "10000", "INCREMENT_BY": "1", "LAST_NUMBER": "8500",
                     "CYCLE_FLAG": "N"}]
        return []


def test_lookup_sequence_capacity_alert():
    from contextos.lineage.tools import lookup_sequence
    from contextos.lineage import store
    from contextos.storage.db import make_engine
    eng = make_engine("sqlite://")
    store.create_all(eng)
    r = lookup_sequence(eng, name="ORDER_SEQ", router=_SingleRouter(_SeqQuerier()))
    cap = r["sequence"]["capacity"]
    assert round(cap["usage_pct"], 1) == 85.0
    assert cap["alert"] is True                    # >80% 告警
    assert cap["cycle"] is False


def test_lookup_sequence_capacity_offline_none():
    """离线(router=None): sequence=None, 不算容量, 不抛。"""
    from contextos.lineage.tools import lookup_sequence
    from contextos.lineage import store
    from contextos.storage.db import make_engine
    eng = make_engine("sqlite://")
    store.create_all(eng)
    r = lookup_sequence(eng, name="ORDER_SEQ", router=None)
    assert r["sequence"] is None
    assert r["note"] == "oracle_offline"
