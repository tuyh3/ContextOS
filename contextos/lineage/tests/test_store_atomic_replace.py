"""HIGH-2: store 层元数据全量覆盖必须原子(clear + write + set_meta 单事务)。

设计意图:
  "拉失败绝不清空旧快照"承诺过去只守 fetch 侧(fetch 在 clear 之前)。write 侧无保护:
  原实现 clear_* / write_* / set_meta 各自独立 engine.begin(), clear 一提交即可见,
  之后任一 write 抛异常 -> 旧快照已清 + 新数据只写一半 = 残缺快照(时间戳还停在旧值)。
  红线#6 生产落地是信创 PG, 严格 PG 会对超长 Oracle 值(如 ALL_DB_LINKS.HOST 完整 TNS
  描述符 >512)抛 DataError, 真库写入即触发, 不是合成 bug。

评分/通过标准:
  1. 注入'写到一半抛异常'后, 旧快照(列/视图/表/owner_routing)必须原封不动。
  2. 时间戳必须停在旧值(回滚后未盖新)。
  3. 正向(无失败)路径仍全量覆盖且盖新时间戳。

自动脚本测试逻辑:
  - make_engine("sqlite://") 同引擎跨 begin 共享状态: 非原子实现下 clear 会提交并对后续
    读可见, 故能区分'原子回滚'(读到旧)与'半清空'(读到残缺)。
  - 通过 monkeypatch store._insert_rows_conn 注入特定表的写失败(身份比较, 不靠表名串);
    模拟 PG 写入侧 DataError, 该 seam 是 clear 之后才发生的写阶段。
  - fixture 用中性合成名(OWNER_X/OLD_T/NEW_T 等), 不掺真实客户 schema。
"""
from __future__ import annotations

import pytest

from contextos.lineage import store
from contextos.storage.db import make_engine


def _engine():
    e = make_engine("sqlite://")
    store.create_all(e)
    return e


def test_replace_object_metadata_atomic_rolls_back_on_write_failure(monkeypatch):
    e = _engine()
    # 旧快照: 1 列 + 1 视图 + 时间戳
    store.write_columns(e, [dict(owner="OWNER_X", table_name="OLD_T", column_name="OLD_C",
                                 data_type="X", nullable="Y", comment="", column_id=1, db_name="DB1")])
    store.write_views(e, [dict(owner="OWNER_X", view_name="OLD_V", comment="", db_name="DB1")])
    store.set_meta(e, "object_metadata_refreshed_at", "2026-01-01T00:00:00")

    # 注入: 写 procedures 表(覆盖序列里 columns/views 之后)时抛 -> 模拟 clear 之后的写入侧失败
    real = store._insert_rows_conn

    def boom(conn, table, rows):
        if table is store.procedures:
            raise RuntimeError("simulated PG DataError mid-write")
        return real(conn, table, rows)

    monkeypatch.setattr(store, "_insert_rows_conn", boom)

    with pytest.raises(RuntimeError):
        store.replace_object_metadata(
            e,
            columns=[dict(owner="OWNER_X", table_name="NEW_T", column_name="NEW_C",
                          data_type="X", nullable="Y", comment="", column_id=1, db_name="DB1")],
            indexes=[], constraints=[], sequences=[], views=[],
            procedures=[dict(owner="OWNER_X", object_name="NEW_P", object_type="PROCEDURE", db_name="DB1")],
            dependencies=[], dblinks=[],
            refreshed_at="2026-06-07T00:00:00")

    # 旧快照原封不动(原子回滚), 时间戳停在旧值
    assert [r["column_name"] for r in store.all_columns(e)] == ["OLD_C"]
    assert [r["view_name"] for r in store.all_views(e)] == ["OLD_V"]
    assert store.get_meta(e, "object_metadata_refreshed_at") == "2026-01-01T00:00:00"


def test_replace_object_metadata_success_overwrites(monkeypatch):
    e = _engine()
    store.write_views(e, [dict(owner="OWNER_X", view_name="OLD_V", comment="", db_name="DB1")])
    store.set_meta(e, "object_metadata_refreshed_at", "2026-01-01T00:00:00")
    store.replace_object_metadata(
        e, columns=[], indexes=[], constraints=[], sequences=[],
        views=[dict(owner="OWNER_X", view_name="NEW_V", comment="", db_name="DB1")],
        procedures=[], dependencies=[], dblinks=[],
        refreshed_at="2026-06-07T00:00:00")
    assert [r["view_name"] for r in store.all_views(e)] == ["NEW_V"]
    assert store.get_meta(e, "object_metadata_refreshed_at") == "2026-06-07T00:00:00"


def test_replace_metadata_atomic_rolls_back_on_write_failure(monkeypatch):
    e = _engine()
    store.write_table_metadata(e, [dict(owner="OWNER_X", template_name="OLD_T", db_name="DB1",
                                        comment="", dataset_type="TABLE")])
    store.set_owner_routing(e, {"OLD_OWNER": "OLD_TNS"})
    store.set_meta(e, "metadata_refreshed_at", "2026-01-01T00:00:00")

    real = store._insert_rows_conn

    def boom(conn, table, rows):
        if table is store.table_synonyms:
            raise RuntimeError("simulated PG DataError mid-write")
        return real(conn, table, rows)

    monkeypatch.setattr(store, "_insert_rows_conn", boom)

    with pytest.raises(RuntimeError):
        store.replace_metadata(
            e,
            tables=[dict(owner="OWNER_X", template_name="NEW_T", db_name="DB1",
                         comment="", dataset_type="TABLE")],
            synonyms=[dict(synonym_name="S1", db_name="DB1", table_owner="OWNER_X",
                           table_name="NEW_T", db_link="")],
            fks=[], owner_tns={"NEW_OWNER": "NEW_TNS"},
            refreshed_at="2026-06-07T00:00:00")

    # 旧表元数据 + owner_routing + 时间戳全保留(原子回滚)
    assert [r["template_name"] for r in store.all_table_metadata(e)] == ["OLD_T"]
    assert store.all_owner_routing(e) == {"OLD_OWNER": "OLD_TNS"}
    assert store.get_meta(e, "metadata_refreshed_at") == "2026-01-01T00:00:00"


def test_replace_metadata_success_overwrites_and_sets_routing():
    e = _engine()
    store.write_table_metadata(e, [dict(owner="OWNER_X", template_name="OLD_T", db_name="DB1",
                                        comment="", dataset_type="TABLE")])
    store.replace_metadata(
        e,
        tables=[dict(owner="OWNER_X", template_name="NEW_T", db_name="DB1",
                     comment="", dataset_type="TABLE")],
        synonyms=[], fks=[], owner_tns={"NEW_OWNER": "NEW_TNS"},
        refreshed_at="2026-06-07T00:00:00")
    assert [r["template_name"] for r in store.all_table_metadata(e)] == ["NEW_T"]
    assert store.all_owner_routing(e) == {"NEW_OWNER": "NEW_TNS"}
    assert store.get_meta(e, "metadata_refreshed_at") == "2026-06-07T00:00:00"


def test_replace_metadata_leaves_owner_routing_untouched_when_owner_tns_none():
    """单库 refresh_metadata 路径不传 owner_tns(None)-> 不动 owner_routing(只 multi 才管路由)。"""
    e = _engine()
    store.set_owner_routing(e, {"KEEP_OWNER": "KEEP_TNS"})
    store.replace_metadata(
        e, tables=[], synonyms=[], fks=[], owner_tns=None,
        refreshed_at="2026-06-07T00:00:00")
    assert store.all_owner_routing(e) == {"KEEP_OWNER": "KEEP_TNS"}
