"""Task 9: NameResolver dblink 解析测试。

设计思路:
  - dblink_index=None 时保持老行为(剥 @ 不解析,向后兼容)。
  - dblink_index={...} 时: resolve_table('表@DBLINK') -> db=目标库 TNS, tpl=表名(不含 @DBLINK)。
  - dblink 解析不出时: 登记 r.unresolved_dblinks set, db 留空。

评分标准:
  1. test_resolve_dblink_sets_target_db — 有 dblink_index 时 db 设为目标库,tpl 去 @ 后缀
  2. test_resolve_unresolved_dblink_registered — 未知 dblink 进 unresolved_dblinks set
  3. test_resolve_no_dblink_unchanged — None 时保持老行为(剥 @ 但 db='')

自动脚本测试逻辑:
  - _resolver() 辅助函数统一建 sqlite:// + create_all + 构造 NameResolver
  - 不依赖真实元数据(离线降级),只测 dblink 路由逻辑
"""
from contextos.lineage.name_resolve import NameResolver
from contextos.lineage import store
from contextos.storage.db import make_engine
from contextos.profile.schema import TablesConfig


def _resolver(dblink_index=None):
    e = make_engine("sqlite://"); store.create_all(e)
    return NameResolver(e, TablesConfig(), dblink_index=dblink_index)


def test_resolve_dblink_sets_target_db():
    r = _resolver(dblink_index={"BILLING": "TEST_DB3"})
    db, owner, tpl, dtype = r.resolve_table("CB_BILL@BILLING")
    assert db == "TEST_DB3"          # 跨库: db = dblink 目标库
    assert tpl == "CB_BILL"           # 表名不含 @ 后缀


def test_resolve_unresolved_dblink_registered():
    r = _resolver(dblink_index={})
    r.resolve_table("CB_BILL@UNKNOWNLINK")
    assert "UNKNOWNLINK" in r.unresolved_dblinks


def test_resolve_no_dblink_unchanged():
    r = _resolver()                    # dblink_index=None -> 老行为(剥 @ 不解析)
    db, owner, tpl, dtype = r.resolve_table("CB_BILL@SOMELINK")
    assert tpl == "CB_BILL" and db == ""


def test_resolve_dblink_empty_string_value_treated_as_unresolved():
    """Profile 含 {'BILLING': ''} 时: key 存在但 value 为空串 -> 不应覆盖 db,
    应登记 unresolved_dblinks。or-falsy 短路会把空串当 None 误命中基名再加入 unresolved;
    显式 key-in 检查后: key=BILLING 存在, target='', falsy -> unresolved。
    """
    r = _resolver(dblink_index={"BILLING": ""})
    db, owner, tpl, dtype = r.resolve_table("ACCT_TABLE@BILLING")
    assert tpl == "ACCT_TABLE"
    # target 为空串 -> db 不覆盖(保持 metadata 结果, 离线时 '')
    assert db == ""
    # 且空串目标登记为 unresolved(key 在 index 中但 value 无效)
    assert "BILLING" in r.unresolved_dblinks


def test_resolve_dblink_empty_string_base_name_treated_as_unresolved():
    """带 domain 后缀的 dblink: BILLING.WORLD -> base=BILLING 映射 '' -> unresolved。"""
    r = _resolver(dblink_index={"BILLING": ""})
    r.resolve_table("ACCT_TABLE@BILLING.WORLD")
    # base-name 查到空串 -> 不命中有效 target -> unresolved
    assert "BILLING.WORLD" in r.unresolved_dblinks
