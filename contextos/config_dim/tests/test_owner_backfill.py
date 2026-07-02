"""W6: owner_backfill 单测(spec §5.2 + 05 §12.4)。

设计思路:
- backfill_owners 遍历 05 裸名边(src/dst_owner='')-> 该 edge 全部 distinct module
  (evidence_ref 取首段)-> datasource_map[module] 连接身份 -> owner_overlay.resolve_side
  (注入 fake synonym_lookup, 离线)-> write_resolution 落 06 overlay 表。
- HIGH 1(R3)对抗: 一 edge 多 module(多 evidence)各一条 scoped owner_resolution
  (复合 PK 不互覆盖), 守 fetchone 退化不复发。

评分标准:
- test_module_hint: evidence_ref 'cust/impl/Dao.java:42' -> 'cust'(同 Plan 05 source_path 取首段)。
- test_backfill_bare_owner_edge_via_synonym: 裸名边经 synonym 解析 owner=PARTY/source=synonym。
- test_backfill_same_edge_two_modules_two_resolutions: 两 module 各一条(n==2, 非 fetchone 的 1),
  各 datasource 身份各自解析 owner。

红线: 存储走 SQLAlchemy(create_engine sqlite:///:memory: 仅单测内存库, 非裸 SQLite 文件 IO);
synonym 注入(离线 fake), 不直连 Oracle。
"""
from sqlalchemy import create_engine, insert, select
from contextos.lineage import store as L
from contextos.config_dim.schema import metadata as M06, owner_resolution
from contextos.config_dim.owner_backfill import backfill_owners, module_hint


def test_module_hint():
    assert module_hint("cust/impl/PmOfferDao.java:42") == "cust"
    assert module_hint("order/x.java") == "order"


def test_backfill_bare_owner_edge_via_synonym():
    e05 = create_engine("sqlite:///:memory:"); L.metadata.create_all(e05)
    e06 = create_engine("sqlite:///:memory:"); M06.create_all(e06)
    with e05.begin() as c:
        c.execute(insert(L.lineage_edges).values(edge_id="E1", src_table="CB_CUSTOMER",
                                                 dst_table="X", src_owner="", dst_owner=""))
        c.execute(insert(L.lineage_evidence).values(edge_id="E1", evidence_ref="cust/impl/Dao.java:10"))
    # datasource_map: module cust -> 连接 et @ crmdev1; synonym 把 et 的 CB_CUSTOMER 指到 PARTY
    dmap = {"cust": {"user": "ET", "datasource_key": "crmdev1"}}
    syn = lambda user, table: "PARTY" if table == "CB_CUSTOMER" else None
    n = backfill_owners(e05, dmap, syn, e06)
    assert n >= 1
    with e06.connect() as c:
        rows = list(c.execute(select(owner_resolution)))
    r = [x for x in rows if x.edge_id == "E1"][0]
    assert r.module == "cust" and r.resolved_src_owner == "PARTY" and r.src_resolution_source == "synonym"


def test_backfill_same_edge_two_modules_two_resolutions():
    """HIGH 1(R3)对抗: 一 edge 两 evidence(两 module / 两 datasource)-> 两条 scoped
    owner_resolution(复合 PK 不互覆盖), 非只首条 module 一条。守 fetchone 退化不复发。"""
    e05 = create_engine("sqlite:///:memory:"); L.metadata.create_all(e05)
    e06 = create_engine("sqlite:///:memory:"); M06.create_all(e06)
    with e05.begin() as c:
        c.execute(insert(L.lineage_edges).values(edge_id="E1", src_table="CB_CUSTOMER",
                                                 dst_table="X", src_owner="", dst_owner=""))
        c.execute(insert(L.lineage_evidence).values(edge_id="E1", evidence_ref="cust/impl/Dao.java:10"))
        c.execute(insert(L.lineage_evidence).values(edge_id="E1", evidence_ref="order/impl/Svc.java:20"))
    dmap = {"cust": {"user": "ET", "datasource_key": "crmdev1"},
            "order": {"user": "ORD", "datasource_key": "orddev1"}}
    syn = lambda user, table: {"ET": "PARTY", "ORD": "ORDOWN"}.get(user) if table == "CB_CUSTOMER" else None
    n = backfill_owners(e05, dmap, syn, e06)
    assert n == 2  # 两 module 各一条(非 fetchone 的 1)
    with e06.connect() as c:
        rows = list(c.execute(select(owner_resolution).where(owner_resolution.c.edge_id == "E1")))
    assert {r.module for r in rows} == {"cust", "order"}  # 复合 PK 不互覆盖
    by_mod = {r.module: r.resolved_src_owner for r in rows}
    assert by_mod["cust"] == "PARTY" and by_mod["order"] == "ORDOWN"  # 各 datasource 身份各自解析
