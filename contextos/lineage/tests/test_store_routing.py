"""test_store_routing.py — Task 5 TDD: owner_routing 表 helper 验证。

设计目标: owner_routing 独立存储 owner->TNS 映射, 不进 _DATA_TABLES / _OBJECT_META_TABLES,
          由多库 refresh 自管 set_owner_routing(幂等全量覆盖), 避免单库 clear_all 误清路由。

set_owner_routing 语义: 内部先 delete(owner_routing) 再 insert, 一个事务完成, 上层
无需手动调 clear_owner_routing(clear_owner_routing 保留但非必须前置)。
"""
from contextos.lineage import store
from contextos.storage.db import make_engine


def _eng():
    e = make_engine("sqlite://")
    store.create_all(e)
    return e


def test_owner_routing_set_get_all():
    e = _eng()
    store.set_owner_routing(e, {"UPC": "TEST_DB1", "SEC": "TEST_DB3"})
    assert store.all_owner_routing(e) == {"UPC": "TEST_DB1", "SEC": "TEST_DB3"}


def test_owner_routing_clear_then_set_overwrites():
    """clear_owner_routing + set_owner_routing 组合仍正确(clear 保留作显式清空 API)。"""
    e = _eng()
    store.set_owner_routing(e, {"UPC": "A"})
    store.clear_owner_routing(e)
    store.set_owner_routing(e, {"SEC": "B"})
    assert store.all_owner_routing(e) == {"SEC": "B"}


def test_owner_routing_set_idempotent_no_clear():
    """set_owner_routing 自包含: 不调 clear_owner_routing 直接二次写同 owner 不崩且全量覆盖。

    修复前行为: 第二次调用触发 PK UNIQUE 约束 IntegrityError。
    修复后语义: 内部先 delete 再 insert(同一事务), 幂等, 返回最新 mapping。
    """
    e = _eng()
    store.set_owner_routing(e, {"UPC": "A", "SEC": "B"})
    # 不调 clear_owner_routing, 直接覆盖写(含新增 + 旧 owner 消失)
    store.set_owner_routing(e, {"UPC": "C"})
    result = store.all_owner_routing(e)
    assert result == {"UPC": "C"}, f"expected {{'UPC': 'C'}}, got {result}"
