from contextos.lineage import store
from contextos.storage.db import make_engine


def _eng():
    e = make_engine("sqlite://")
    store.create_all(e)
    return e


class _FakeObjQuerier:
    """空实现: 所有对象元数据查询返空行, 模拟 fetch_object_metadata 成功但无数据的最小路径。"""
    def query(self, sql, params=None):
        return []


def test_dblinks_write_all_roundtrip():
    e = _eng()
    store.write_dblinks(e, [dict(owner="UPC", db_link="BILLING.WORLD",
                                 host="BILLINGDB", username="RPT", created="2020-01-01",
                                 db_name="CCRM3")])
    rows = store.all_dblinks(e)
    assert len(rows) == 1 and rows[0]["db_link"] == "BILLING.WORLD"


def test_unresolved_dblinks_write_all_roundtrip():
    e = _eng()
    store.write_unresolved_dblinks(e, [dict(db_link="X.WORLD", host="ZZZ",
                                            reason="no_matching_instance", db_name="CCRM3")])
    assert len(store.all_unresolved_dblinks(e)) == 1


def test_clear_object_metadata_clears_dblinks():
    e = _eng()
    store.write_dblinks(e, [dict(owner="UPC", db_link="L", host="H",
                                 username="U", created="", db_name="D")])
    store.clear_object_metadata(e)
    assert store.all_dblinks(e) == []


def test_clear_all_clears_unresolved_dblinks():
    e = _eng()
    store.write_unresolved_dblinks(e, [dict(db_link="L", host="H", reason="r", db_name="D")])
    store.clear_all(e)
    assert store.all_unresolved_dblinks(e) == []


def test_refresh_object_metadata_clears_and_rewrites_dblinks():
    """Task 6 完成: refresh_object_metadata 现在有 dblinks 写回路径。
    clear_object_metadata 清空旧 dblinks 后, fetch_object_metadata 查 ALL_DB_LINKS
    并写回新值。_FakeObjQuerier 对 ALL_DB_LINKS 返空行, 所以 refresh 后 dblinks 为空
    (旧记录被清, 新查询无结果) -- 正确语义: 全量快照覆盖, 不保留刷新前写入的记录。
    """
    from contextos.lineage.oracle_metadata import refresh_object_metadata

    e = _eng()
    # 先写入一条 dblink 记录
    store.write_dblinks(e, [dict(owner="UPC", db_link="BILLING.WORLD",
                                 host="BILLINGDB", username="RPT", created="2020-01-01",
                                 db_name="CCRM3")])
    assert len(store.all_dblinks(e)) == 1  # 写入成功

    # refresh_object_metadata 用空 querier(所有查询含 ALL_DB_LINKS 返空行)
    out = refresh_object_metadata(_FakeObjQuerier(), e, owners=["UPC"], db_name="CCRM3",
                                  now="2026-06-06T00:00:00")
    assert out["refreshed"] is True  # refresh 成功(非连接失败)

    # 全量覆盖: 旧 dblink 被 clear 清除, querier 返空故无新写入 -> 结果为空
    assert store.all_dblinks(e) == [], "全量覆盖语义: querier 返空时旧记录应被清除"
