"""tools.py 多库路由逻辑测试(Block 1b Task 13)。

设计思路
--------
Task 13 把 lookup_table/lineage/dependency/sequence 的 querier= 参数改名 router=(DbRouter|None)。
本测试验证:
1. lookup_table 按 owner 路由: router.querier_for_owner(owner) 返对应 querier -> 只连那一库。
2. lookup_table offline(router=None) -> note=oracle_offline(保留离线降级契约)。
3. _route helper: owner 可定位 -> 单库 querier; 否则 fan-out 合并。
4. fan-out 韧性: 第一个库查询期抛 RuntimeError(模拟 ORA-03113 / ORA-12541 半宕态),
   第二个库健康 -> 四个 lookup 函数均跳过坏库继续返健康库数据,不 raise。

评分标准
--------
- router 传入时按 owner 路由到正确库(不查其他库)。
- router=None -> note=oracle_offline,与旧 querier=None 行为等价。
- 第一库 query 抛 RuntimeError -> 不逃逸整个 lookup; 返回来自第二库的数据。

fixture 用中性合成名(T_UPC/T_SEC/UPC/SEC),不含真客户值。

自动脚本逻辑
------------
内存 SQLite + store + owner_routing 表;DbRouter 注入 fake connect 函数(返 _Q 对象);
_Q.query 按 ALL_TAB_COLUMNS 关键词返 canned 列名;router 路由 UPC -> "A" 库(qa) 而非 "B" 库(qb)。
韧性测试用 _FanOutRouter(不依赖 DbRouter): fan_out 直接返 [坏库, 好库] 列表,
坏库 query 方法无条件抛 RuntimeError,好库返 canned 数据。
"""
from __future__ import annotations

from typing import Any

from contextos.lineage import store, tools
from contextos.lineage.db_router import DbRouter
from contextos.storage.db import make_engine


class _Prof:
    class oracle:
        allowed_instances = ["A", "B"]


class _Q:
    def __init__(self, tag: str, cols: list[dict[str, Any]]) -> None:
        self.tag = tag
        self.cols = cols

    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        if "ALL_TAB_COLUMNS" in sql.upper():
            return self.cols
        if "ALL_TAB_COMMENTS" in sql.upper():
            return [{"COMMENTS": f"comment from {self.tag}"}]
        return []


def _engine_with_routing() -> Any:
    e = make_engine("sqlite://")
    store.create_all(e)
    store.set_owner_routing(e, {"UPC": "A"})
    store.write_table_metadata(e, [
        dict(owner="UPC", template_name="T", db_name="CCRM3",
             comment="", dataset_type="TABLE"),
    ])
    return e


def test_lookup_table_routes_by_owner() -> None:
    """lookup_table: owner 已知 -> 只连 owner 对应的库 A,不连 B。"""
    e = _engine_with_routing()
    qa = _Q("A", [{"COLUMN_NAME": "C1", "DATA_TYPE": "VARCHAR2"}])
    qb = _Q("B", [{"COLUMN_NAME": "WRONG", "DATA_TYPE": "X"}])
    r = DbRouter(_Prof(), e, connect=lambda tns: {"A": qa, "B": qb}[tns])
    out = tools.lookup_table(e, table="T", owner="UPC", router=r)
    assert out["columns"] == [{"column_name": "C1", "data_type": "VARCHAR2"}]


def test_lookup_table_offline_router_none() -> None:
    """router=None -> note=oracle_offline(向后兼容)。"""
    e = _engine_with_routing()
    out = tools.lookup_table(e, table="T", router=None)
    assert out["note"] == "oracle_offline"


# ----------------------------------------------------------------- fan-out resilience


class _BrokenQ:
    """第一个库: query 方法无条件抛 RuntimeError(模拟 ORA-03113 half-dead standby)。"""

    def query(self, sql: str, params: Any = None) -> list[Any]:
        raise RuntimeError("simulated ORA-03113 end-of-file on communication channel")


class _HealthyQ:
    """第二个库: 返 canned 数据,记录调用次数。"""

    def __init__(self) -> None:
        self.calls: int = 0

    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        self.calls += 1
        upper = sql.upper()
        if "ALL_TAB_COLUMNS" in upper:
            return [{"COLUMN_NAME": "ID", "DATA_TYPE": "NUMBER"}]
        if "ALL_TAB_COMMENTS" in upper:
            return [{"COMMENTS": "healthy comment"}]
        if "ALL_DEPENDENCIES" in upper:
            return [{"OWNER": "APP", "NAME": "V_HEALTHY", "TYPE": "VIEW",
                     "REFERENCED_NAME": "T"}]
        if "ALL_SYNONYMS" in upper:
            return []
        if "ALL_SEQUENCES" in upper:
            return [{"SEQUENCE_OWNER": "APP", "SEQUENCE_NAME": "T",
                     "MIN_VALUE": 1, "MAX_VALUE": 9999, "INCREMENT_BY": 1,
                     "LAST_NUMBER": 100, "CYCLE_FLAG": "N"}]
        return []


class _FanOutRouter:
    """不依赖 DbRouter: fan_out 直接返 [broken, healthy],模拟两库 fan-out。"""

    def __init__(self, broken: Any, healthy: Any) -> None:
        self._broken = broken
        self._healthy = healthy

    def resolve_owner_for_table(self, table: str) -> None:
        return None

    def querier_for_owner(self, owner: str) -> None:
        return None

    def fan_out(self) -> list[Any]:
        return [self._broken, self._healthy]


def _engine_for_resilience() -> Any:
    e = make_engine("sqlite://")
    store.create_all(e)
    return e


def test_lookup_table_fanout_skips_broken_querier() -> None:
    """lookup_table: 第一库 query 抛异常 -> 跳过, 返第二库的 columns 数据, 不 raise。"""
    e = _engine_for_resilience()
    broken = _BrokenQ()
    healthy = _HealthyQ()
    router = _FanOutRouter(broken, healthy)
    out = tools.lookup_table(e, table="T", router=router)
    assert out["columns"] == [{"column_name": "ID", "data_type": "NUMBER"}]
    assert healthy.calls >= 1


def test_lookup_lineage_fanout_skips_broken_querier() -> None:
    """lookup_lineage: 第一库 ALL_DEPENDENCIES 抛 -> 跳过, 第二库数据进 upstream, 不 raise。"""
    e = _engine_for_resilience()
    broken = _BrokenQ()
    healthy = _HealthyQ()
    router = _FanOutRouter(broken, healthy)
    out = tools.lookup_lineage(e, table="T", router=router)
    assert any(u["table"] == "V_HEALTHY" for u in out["upstream"])
    assert healthy.calls >= 1


def test_lookup_dependency_fanout_skips_broken_querier() -> None:
    """lookup_dependency: 第一库抛 -> 跳过, 第二库 dependents 累加, 不 raise。"""
    e = _engine_for_resilience()
    broken = _BrokenQ()
    healthy = _HealthyQ()
    router = _FanOutRouter(broken, healthy)
    out = tools.lookup_dependency(e, name="T", router=router)
    assert any(d["name"] == "V_HEALTHY" for d in out["dependents"])
    assert healthy.calls >= 1


def test_lookup_sequence_fanout_skips_broken_querier() -> None:
    """lookup_sequence: 第一库 ALL_SEQUENCES 抛 -> 跳过, 第二库返 sequence dict, 不 raise。"""
    e = _engine_for_resilience()
    broken = _BrokenQ()
    healthy = _HealthyQ()
    router = _FanOutRouter(broken, healthy)
    out = tools.lookup_sequence(e, name="T", router=router)
    assert out["sequence"] is not None
    assert out["sequence"]["sequence_name"] == "T"
    assert healthy.calls >= 1
