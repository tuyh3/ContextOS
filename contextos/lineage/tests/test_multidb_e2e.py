"""Block 1b 多库 + dblink + 查询路由 端到端 smoke(纯 fake querier, 不连真库)。

串: refresh_metadata_multi -> refresh_object_metadata_multi -> build_object_lineage(带 dblink_index)
-> DbRouter 路由查询, 验两库元数据共存 + owner 路由 + 跨库 dblink 边(src_db != dst_db)。
"""
from dataclasses import dataclass

from contextos.lineage import object_lineage, oracle_metadata, store
from contextos.lineage.db_router import DbRouter
from contextos.profile.schema import TablesConfig
from contextos.storage.db import make_engine


@dataclass
class _Spec:
    tns: str
    db_name: str
    owners: list[str]


class _Q:
    """按 owner 回声一张表的 fake querier(两库各自的 owner)。"""

    def __init__(self, tns: str):
        self.tns = tns

    def query(self, sql, params=None):
        # 方案 B 批量: 从 OWNER IN (:o0,...) 的 o-bind 派生 owner, 每 owner 返一行。
        owners = [v for k, v in (params or {}).items() if k[:1] == "o" and k[1:].isdigit()]
        if "ALL_TAB_COMMENTS" in sql:
            return [{"OWNER": o, "TABLE_NAME": f"T_{o}",
                     "TABLE_TYPE": "TABLE", "COMMENTS": ""} for o in owners]
        if "ALL_TAB_COLUMNS" in sql:
            return [{"OWNER": o, "TABLE_NAME": f"T_{o}", "COLUMN_NAME": "C",
                     "DATA_TYPE": "X", "NULLABLE": "Y", "COLUMN_ID": 1, "COMMENTS": ""}
                    for o in owners]
        return []


class _Prof:
    class oracle:
        allowed_instances = ["A", "B"]


def test_multidb_end_to_end():
    e = make_engine("sqlite://")
    store.create_all(e)
    specs = [_Spec("A", "CCRM3", ["UPC"]), _Spec("B", "VCDB", ["SEC"])]
    oracle_metadata.refresh_metadata_multi(
        e, specs, querier_factory=lambda t: _Q(t), now="2026-06-06T00:00:00")
    oracle_metadata.refresh_object_metadata_multi(
        e, specs, querier_factory=lambda t: _Q(t), now="2026-06-06T00:00:00")

    # 两库元数据共存 + owner 路由建好
    assert {r["db_name"] for r in store.all_table_metadata(e)} == {"CCRM3", "VCDB"}
    assert store.all_owner_routing(e) == {"UPC": "A", "SEC": "B"}

    # 跨库 dblink 对象依赖: 本地 UPC.V_X 经 dblink LNK 引用 B 库的 T_SEC
    store.write_dependencies(e, [dict(owner="UPC", name="V_X", type="VIEW",
                                      referenced_owner="SEC", referenced_name="T_SEC",
                                      referenced_type="TABLE", referenced_link_name="LNK",
                                      db_name="CCRM3")])
    object_lineage.build_object_lineage(e, TablesConfig(), now="2026-06-06T00:00:00",
                                        dblink_index={"LNK": "B"})
    obj_edges = [x for x in store.all_edges(e) if x["edge_kind"] == "OBJECT_DEPENDENCY"]
    assert obj_edges and obj_edges[0]["dst_db"] == "B"
    assert obj_edges[0]["src_db"] != obj_edges[0]["dst_db"]

    # 查询期路由: T_UPC -> owner UPC -> 库 A
    r = DbRouter(_Prof(), e, connect=lambda tns: _Q(tns))
    assert r.resolve_owner_for_table("T_UPC") == "UPC"
    assert r.querier_for_owner("UPC").tns == "A"
