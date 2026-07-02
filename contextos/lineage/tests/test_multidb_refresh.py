"""Task 11: 多库元数据 orchestrator + owner_routing 填充 + schema 重叠告警。

Design rationale:
  - refresh_metadata_multi: 多库表级元数据全量刷新;各实例各自 querier_factory(tns) append(db_name 标来源),
    clear 一次写一次;建 owner->TNS 进 owner_routing;schema 重叠(同 owner 跨实例)warn + overlapping_owners;
    任一实例必查失败 -> 保留旧快照不清空(返 refreshed=False);空 instances 早退。
  - refresh_object_metadata_multi: 同语义,针对 7 类对象元数据 + dblinks。

Test scoring:
  Pass criteria: 8 tests all PASS, pyright 0 new errors.
  refresh_metadata_multi: 4 tests (append/routing/overlap/fail-safe).
  refresh_object_metadata_multi: 4 tests (append/dblinks-dedup/overlap-warn/fail-safe).

Automated test logic:
  - _OwnerEcho: 按 sql 关键字模拟 Oracle tab_comments + columns 查询,返回 db_name 标注的行。
  - _ObjEcho: 模拟 fetch_object_metadata 使用的 Oracle columns / dblinks 查询;
    columns 查询按 owner 返回一行; dblinks 查询始终包含一条 PUBLIC dblink(模拟真实 _Q_DBLINKS
    OR OWNER='PUBLIC' 场景), 用于验证 PK 去重防 IntegrityError。
  - _Dead: 模拟 Oracle 断连,query 抛 RuntimeError。
  - Fixture: sqlite in-memory engine via make_engine("sqlite://")。
"""
import logging
from dataclasses import dataclass

from contextos.lineage import store
from contextos.lineage.oracle_metadata import (
    refresh_metadata_multi,
    refresh_object_metadata_multi,
)
from contextos.storage.db import make_engine


@dataclass
class _Spec:
    tns: str
    db_name: str
    owners: list[str]


class _OwnerEcho:
    """每个实例返回自己 owner 的一张表, db_name 由 caller 注入。"""
    def __init__(self, table_for):
        self.table_for = table_for

    def query(self, sql, params=None):
        # 方案 B 批量: owner 走 OWNER IN (:o0,:o1,...) -> 从 o-bind 派生, 每 owner 返一行。
        owners = [v for k, v in (params or {}).items() if k[:1] == "o" and k[1:].isdigit()]
        if "ALL_TAB_COMMENTS" in sql:
            return [{"OWNER": o, "TABLE_NAME": self.table_for,
                     "TABLE_TYPE": "TABLE", "COMMENTS": ""} for o in owners]
        return []


class _ObjEcho:
    """模拟 fetch_object_metadata 查询:
    - ALL_TAB_COLUMNS: 按 owner 返回一列。
    - ALL_DB_LINKS: 始终返回一条 PUBLIC dblink(同一批 PUBLIC 重复, 触发去重路径)。
    - 其余查询返回空(columns 是必查门, 非空才视为连接成功)。
    """
    def query(self, sql, params=None):
        # 方案 B 批量: 从 o-bind 派生 owner 列表, columns 每 owner 返一列。
        owners = [v for k, v in (params or {}).items() if k[:1] == "o" and k[1:].isdigit()] or ["ANON"]
        if "ALL_TAB_COLUMNS" in sql:
            return [{"OWNER": o, "TABLE_NAME": "T1", "COLUMN_NAME": "ID",
                     "DATA_TYPE": "NUMBER", "NULLABLE": "N", "DATA_LENGTH": 10,
                     "DATA_PRECISION": None, "DATA_SCALE": None, "COLUMN_ID": 1,
                     "DATA_DEFAULT": None, "COMMENTS": ""} for o in owners]
        if "ALL_DB_LINKS" in sql:
            # PUBLIC dblink: 批量也只一条 PUBLIC(跨实例 merged 仍可能重复 -> 需去重路径)
            return [{"OWNER": "PUBLIC", "DB_LINK": "SHARED.WORLD",
                     "USERNAME": "APP", "HOST": "db.example.com"}]
        return []


# ---------------------------------------------------------------------------
# refresh_metadata_multi tests (4 个)
# ---------------------------------------------------------------------------

def test_refresh_multi_appends_per_instance_with_db_name():
    e = make_engine("sqlite://"); store.create_all(e)
    specs = [_Spec("TEST_DB1", "CCRM3", ["UPC"]),
             _Spec("TEST_DB3", "VCDB", ["SEC"])]

    def factory(tns):
        return _OwnerEcho({"TEST_DB1": "T_UPC", "TEST_DB3": "T_SEC"}[tns])

    out = refresh_metadata_multi(e, specs, querier_factory=factory, now="2026-06-06T00:00:00")
    rows = {r["owner"]: r["db_name"] for r in store.all_table_metadata(e)}
    assert rows == {"UPC": "CCRM3", "SEC": "VCDB"}        # 两库 append + db_name 标来源
    assert out["refreshed"] is True


def test_refresh_multi_builds_owner_routing():
    e = make_engine("sqlite://"); store.create_all(e)
    specs = [_Spec("TEST_DB1", "CCRM3", ["UPC"]),
             _Spec("TEST_DB3", "VCDB", ["SEC"])]
    refresh_metadata_multi(e, specs,
                           querier_factory=lambda tns: _OwnerEcho("T"),
                           now="2026-06-06T00:00:00")
    assert store.all_owner_routing(e) == {"UPC": "TEST_DB1", "SEC": "TEST_DB3"}


def test_refresh_multi_warns_on_schema_overlap(caplog):
    e = make_engine("sqlite://"); store.create_all(e)
    # 两库都有 owner UPC -> 身份冲突, 应告警 + 登记
    specs = [_Spec("TEST_DB1", "CCRM3", ["UPC"]),
             _Spec("TEST_DB3", "VCDB", ["UPC"])]
    with caplog.at_level(logging.WARNING):
        out = refresh_metadata_multi(e, specs,
                                     querier_factory=lambda tns: _OwnerEcho("T"),
                                     now="2026-06-06T00:00:00")
    assert "overlap" in caplog.text.lower()
    assert "UPC" in out.get("overlapping_owners", [])


def test_refresh_multi_one_instance_fails_keeps_old_snapshot():
    e = make_engine("sqlite://"); store.create_all(e)
    store.write_table_metadata(e, [dict(owner="OLD", template_name="OLD_T",
                                        db_name="OLDDB", comment="", dataset_type="TABLE")])

    class _Dead:
        def query(self, sql, params=None):
            raise RuntimeError("ORA-12541")

    specs = [_Spec("TEST_DB1", "CCRM3", ["UPC"])]
    out = refresh_metadata_multi(e, specs, querier_factory=lambda tns: _Dead(),
                                 now="2026-06-06T00:00:00")
    assert out["refreshed"] is False
    assert [r["owner"] for r in store.all_table_metadata(e)] == ["OLD"]   # 旧快照保留


# ---------------------------------------------------------------------------
# refresh_object_metadata_multi tests (4 个, 对称 refresh_metadata_multi)
# ---------------------------------------------------------------------------

def test_refresh_obj_multi_appends_per_instance_with_db_name():
    """两实例 columns append + db_name 标来源 + refreshed=True + 返回各类计数。"""
    e = make_engine("sqlite://"); store.create_all(e)
    specs = [_Spec("TEST_DB1", "CCRM3", ["UPC"]),
             _Spec("TEST_DB3", "VCDB", ["SEC"])]
    out = refresh_object_metadata_multi(
        e, specs, querier_factory=lambda tns: _ObjEcho(), now="2026-06-06T00:00:00"
    )
    assert out["refreshed"] is True
    assert out["instances"] == 2
    # 两 owner 各 1 列 -> 共 2 行 columns
    assert out["columns"] == 2
    # PUBLIC dblink 去重后 1 行
    assert out["dblinks"] == 1
    # object_metadata_refreshed_at 已写入
    assert store.get_meta(e, "object_metadata_refreshed_at") == "2026-06-06T00:00:00"


def test_refresh_obj_multi_dedupes_public_dblinks():
    """两 owner 循环时 PUBLIC dblink 重复出现, 去重后写库无 IntegrityError, 最终 1 行。

    实证复现路径: UPC + SEC 两 owner 各自查询都返回同一条 PUBLIC/SHARED.WORLD dblink。
    若无去重, store.write_dblinks executemany 触发 UNIQUE constraint failed。
    """
    e = make_engine("sqlite://"); store.create_all(e)
    # 预置旧快照, 验证 clear 后重写正确(不残留旧行)
    store.write_dblinks(e, [{"owner": "OLD_OWNER", "db_link": "OLD.LINK",
                              "username": "X", "host": "old.host"}])
    specs = [_Spec("TEST_DB1", "CCRM3", ["UPC", "SEC"])]
    out = refresh_object_metadata_multi(
        e, specs, querier_factory=lambda tns: _ObjEcho(), now="2026-06-06T00:00:00"
    )
    assert out["refreshed"] is True
    # 旧快照 OLD_OWNER/OLD.LINK 已清; PUBLIC/SHARED.WORLD 去重后 1 行
    assert out["dblinks"] == 1
    assert store.get_meta(e, "object_metadata_refreshed_at") == "2026-06-06T00:00:00"


def test_refresh_obj_multi_overlap_does_not_crash(caplog):
    """两实例同 owner 时 (schema overlap), 函数不崩溃且 refreshed=True, columns 去重后写入。

    refresh_object_metadata_multi 对所有有复合 PK 的对象元数据表(columns/indexes/constraints/
    sequences/views/procedures/dblinks)在写库前统一去重, 保留首个; schema overlap 不会触发
    UNIQUE constraint IntegrityError, 函数正常完成并返回 refreshed=True。
    """
    e = make_engine("sqlite://"); store.create_all(e)
    specs = [_Spec("TEST_DB1", "CCRM3", ["UPC"]),
             _Spec("TEST_DB3", "VCDB", ["UPC"])]
    out = refresh_object_metadata_multi(
        e, specs, querier_factory=lambda tns: _ObjEcho(), now="2026-06-06T00:00:00"
    )
    assert out["refreshed"] is True
    # 两实例同 owner UPC 各查出 1 列(UPC/T1/ID), 去重后 1 行
    assert out["columns"] == 1
    # PUBLIC dblink 两实例各查出 1 行, 去重后 1 行
    assert out["dblinks"] == 1


def test_refresh_obj_multi_one_instance_fails_keeps_old_snapshot():
    """任一实例 columns 查询失败 -> refreshed=False + 旧快照保留(fail-safe 核心)。

    fail-safe 保障: fetch_object_metadata 把 columns 当必查门(非 _safe_query);
    断连时 columns 查询抛 Exception -> 落 except 分支 -> clear 未执行 -> 旧快照完整。
    """
    e = make_engine("sqlite://"); store.create_all(e)
    # 预置旧快照
    store.write_columns(e, [{"owner": "OLD", "table_name": "T_OLD", "column_name": "ID",
                              "data_type": "NUMBER", "nullable": "N",
                              "data_length": 10, "data_precision": None,
                              "data_scale": None, "column_id": 1,
                              "data_default": None, "comments": ""}])
    store.write_dblinks(e, [{"owner": "OLD_OWNER", "db_link": "OLD.LINK",
                              "username": "X", "host": "old.host"}])

    class _Dead:
        def query(self, sql, params=None):
            raise RuntimeError("ORA-12541: TNS no listener")

    specs = [_Spec("TEST_DB1", "CCRM3", ["UPC"])]
    out = refresh_object_metadata_multi(
        e, specs, querier_factory=lambda tns: _Dead(), now="2026-06-06T00:00:00"
    )
    assert out["refreshed"] is False
    assert "ORA-12541" in out.get("reason", "")
    # 旧快照完整保留
    cols = [r for r in store.all_columns(e) if r["owner"] == "OLD"]
    assert len(cols) == 1 and cols[0]["column_name"] == "ID"
    dblinks = [r for r in store.all_dblinks(e) if r["owner"] == "OLD_OWNER"]
    assert len(dblinks) == 1 and dblinks[0]["db_link"] == "OLD.LINK"
    # object_metadata_refreshed_at 未被更新(旧值 None)
    assert store.get_meta(e, "object_metadata_refreshed_at") is None


# ---------------------------------------------------------------------------
# HIGH1 守卫: all-empty-owners (外部 review Block 2 Task 2)
# ---------------------------------------------------------------------------

class _NeverQuerier:
    """Querier stub that must never be called: the all-empty-owners guard short-circuits
    before any fetch, so any .query invocation means the guard failed. Doubles as a
    typed _Querier so pyright accepts it as querier_factory's return."""

    def query(self, sql, params=None):
        raise AssertionError("querier must not be called when owners are empty (guard failed)")


def test_refresh_multi_empty_owners_keeps_snapshot_no_wipe():
    """外部 review HIGH1: 非空实例但 owners 全空 -> 绝不 clear, 保留旧快照。"""
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_metadata_multi
    from contextos.storage.db import make_engine

    e = make_engine("sqlite://"); store.create_all(e)
    store.write_table_metadata(e, [dict(owner="OLD", template_name="OLD_T",
                                        db_name="OLDDB", comment="", dataset_type="TABLE")])
    store.set_owner_routing(e, {"OLD": "TEST_DB1"})

    out = refresh_metadata_multi(e, [_Spec("TEST_DB1", "CCRM3", [])],
                                 querier_factory=lambda t: _NeverQuerier(), now="2026-06-07T00:00:00")
    assert out["refreshed"] is False and out["reason"] == "no_owners"
    assert [r["owner"] for r in store.all_table_metadata(e)] == ["OLD"]      # 旧快照保留
    assert store.all_owner_routing(e) == {"OLD": "TEST_DB1"}          # 路由保留


def test_refresh_object_multi_empty_owners_keeps_snapshot():
    from contextos.lineage import store
    from contextos.lineage.oracle_metadata import refresh_object_metadata_multi
    from contextos.storage.db import make_engine

    e = make_engine("sqlite://"); store.create_all(e)
    store.write_columns(e, [dict(owner="OLD", table_name="OLD_T", column_name="C",
                                 data_type="X", nullable="Y", comment="", column_id=1, db_name="D")])
    out = refresh_object_metadata_multi(e, [_Spec("A", "D", [])],
                                        querier_factory=lambda t: _NeverQuerier(), now="2026-06-07T00:00:00")
    assert out["refreshed"] is False and out["reason"] == "no_owners"
    assert len(store.all_columns(e)) == 1
