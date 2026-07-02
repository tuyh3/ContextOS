"""元数据刷新编排测试(TTL + 全量覆盖 + 离线保留旧快照)。时间用注入的 ISO 串, 确定化。"""
from contextos.storage.db import make_engine
from contextos.lineage import store


class FakeQuerier:
    """canned 元数据; fail=True 模拟 Oracle 断连(强制 tab 查询抛)。"""
    def __init__(self, fail=False):
        self.fail = fail

    def query(self, sql, params=None, **kw):
        u = sql.upper()
        if "ALL_TAB_COMMENTS" in u:
            if self.fail:
                raise RuntimeError("ORA-12537 connection closed")
            return [{"OWNER": "UPC", "TABLE_NAME": "PM_OFFER_CHA", "TABLE_TYPE": "TABLE",
                     "COMMENTS": "Offer 渠道授权表"}]
        if "ALL_SYNONYMS" in u:
            return [{"SYNONYM_NAME": "SYN_X", "TABLE_OWNER": "UPC",
                     "TABLE_NAME": "PM_OFFER_CHA", "DB_LINK": None}]
        if "CONSTRAINTS" in u:
            return [{"TABLE_NAME": "PM_OFFER_CHA", "FK_REF_TABLE": "PM_OFFER_BASE"}]
        return []


def _eng():
    e = make_engine("sqlite://")
    store.create_all(e)
    return e


def test_meta_roundtrip_upsert():
    e = _eng()
    assert store.get_meta(e, "k") is None
    store.set_meta(e, "k", "v1")
    store.set_meta(e, "k", "v2")          # upsert, 不 PK 冲突
    assert store.get_meta(e, "k") == "v2"


def test_refresh_idempotent_overwrites():
    from contextos.lineage.oracle_metadata import refresh_metadata
    e = _eng()
    q = FakeQuerier()
    r1 = refresh_metadata(q, e, owners=["UPC"], db_name="CCRM3", now="2026-06-01T00:00:00")
    r2 = refresh_metadata(q, e, owners=["UPC"], db_name="CCRM3", now="2026-06-02T00:00:00")
    assert r1["refreshed"] and r2["refreshed"]
    assert len(store.all_table_metadata(e)) == 1            # 覆盖, 不累积(修 PK bug)
    assert store.get_meta(e, "metadata_refreshed_at") == "2026-06-02T00:00:00"


def test_refresh_keeps_old_snapshot_on_failure():
    """Oracle 断连: 不清空旧快照, 不更新时间戳(production 关键)。"""
    from contextos.lineage.oracle_metadata import refresh_metadata
    e = _eng()
    refresh_metadata(FakeQuerier(), e, owners=["UPC"], db_name="CCRM3", now="2026-06-01T00:00:00")
    assert store.has_metadata(e)
    out = refresh_metadata(FakeQuerier(fail=True), e, owners=["UPC"], db_name="CCRM3",
                           now="2026-06-03T00:00:00")
    assert out["refreshed"] is False
    assert store.has_metadata(e)                            # 旧快照仍在
    assert store.get_meta(e, "metadata_refreshed_at") == "2026-06-01T00:00:00"  # 没更新


def test_is_metadata_stale_ttl():
    from contextos.lineage.oracle_metadata import is_metadata_stale, refresh_metadata
    e = _eng()
    assert is_metadata_stale(e, 24, "2026-06-01T00:00:00") is True   # 从未刷新
    refresh_metadata(FakeQuerier(), e, owners=["UPC"], db_name="CCRM3", now="2026-06-01T00:00:00")
    assert is_metadata_stale(e, 24, "2026-06-01T10:00:00") is False  # 10h < 24h
    assert is_metadata_stale(e, 24, "2026-06-03T00:00:00") is True   # 48h > 24h


def test_refresh_if_stale_skips_when_fresh():
    from contextos.lineage.oracle_metadata import refresh_metadata, refresh_metadata_if_stale
    e = _eng()
    refresh_metadata(FakeQuerier(), e, owners=["UPC"], db_name="CCRM3", now="2026-06-01T00:00:00")
    out = refresh_metadata_if_stale(FakeQuerier(), e, owners=["UPC"], db_name="CCRM3",
                                    ttl_hours=24, now="2026-06-01T05:00:00")
    assert out["refreshed"] is False and out["reason"] == "fresh"


def test_refresh_empty_owners_keeps_snapshot():
    """空 owners(配置错误): 不清空旧快照、不盖时间戳、refreshed=False(2026-06-02 审计加固)。

    防"拉失败绝不清空"承诺的洞: owners=[] 既无拉取也无异常, 旧版会绕过护栏清空 + 误标 fresh。"""
    from contextos.lineage.oracle_metadata import refresh_metadata
    e = _eng()
    refresh_metadata(FakeQuerier(), e, owners=["UPC"], db_name="CCRM3", now="2026-06-01T00:00:00")
    assert store.has_metadata(e)
    out = refresh_metadata(FakeQuerier(), e, owners=[], db_name="CCRM3", now="2026-06-05T00:00:00")
    assert out["refreshed"] is False
    assert out["reason"] == "no_owners"
    assert store.has_metadata(e)                                    # 旧快照仍在, 没被清空
    assert store.get_meta(e, "metadata_refreshed_at") == "2026-06-01T00:00:00"  # 时间戳没更新


class OwnerEchoQuerier:
    """每个 owner 都有一张同名表 COMMON_T。方案 B 批量: 从 OWNER IN (:o0,:o1) 的 o-bind 派生
    owner 列表, 每 owner 返一行带 OWNER(贴近真 Oracle —— ALL_TAB_COMMENTS 总 SELECT OWNER)。"""
    def query(self, sql, params=None, **kw):
        u = sql.upper()
        owners = [v for k, v in (params or {}).items() if k[:1] == "o" and k[1:].isdigit()]
        if "ALL_TAB_COMMENTS" in u:
            return [{"OWNER": o, "TABLE_NAME": "COMMON_T", "TABLE_TYPE": "TABLE",
                     "COMMENTS": "公共表"} for o in owners]
        return []


def test_refresh_keeps_same_table_name_across_owners():
    """裁决 5: 多 owner 同名表 refresh 后各存一行, 不被 _dedupe_by_pk 静默丢。

    回归 review Finding #1: refresh_metadata(owners=[多个]) + _dedupe_by_pk 按裸 template_name
    去重 -> 静默丢一个 owner 的表。"""
    from contextos.lineage.oracle_metadata import refresh_metadata
    e = _eng()
    out = refresh_metadata(OwnerEchoQuerier(), e, owners=["UPC", "SEC"], db_name="",
                           now="2026-06-03T00:00:00")
    assert out["refreshed"] is True
    rows = [r for r in store.all_table_metadata(e) if r["template_name"] == "COMMON_T"]
    assert sorted(r["owner"] for r in rows) == ["SEC", "UPC"]   # 两 owner 各一行, 没丢
    assert out["tables"] == 2


def test_is_metadata_stale_tz_mismatch_is_stale():
    """tz-aware now 与 naive last 相减抛 TypeError -> fail-safe 当 stale, 不崩溃(2026-06-02 审计加固)。"""
    from contextos.lineage.oracle_metadata import is_metadata_stale, refresh_metadata
    e = _eng()
    # naive 时间盖快照(同测试既有风格)
    refresh_metadata(FakeQuerier(), e, owners=["UPC"], db_name="CCRM3", now="2026-06-01T00:00:00")
    # tz-aware now 检查: 不应抛 TypeError, fail-safe 返回 stale(True)重拉
    assert is_metadata_stale(e, 24, "2026-06-01T10:00:00+00:00") is True
