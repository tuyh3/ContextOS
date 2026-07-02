import json
from sqlalchemy import create_engine, insert
from contextos.config_dim.db_snapshot import snapshot_small, snapshot_big, table_to_code


def test_snapshot_small_masks_sensitive_column():
    rows = [{"OFFER_ID": "3001", "CHA_ID": "MZA", "PASSWORD": "supersecret3f7a", "STATE": "A"}]
    items = snapshot_small(rows, pk_cols=["OFFER_ID", "CHA_ID"], db="CCRM3", owner="UPC",
                           table="PM_OFFER_CHA", sensitive_patterns=["password"], salt=b"s")
    it = items[0]
    val = json.loads(it["value_raw"])
    assert val["PASSWORD"].startswith("****")        # 按列掩码
    assert val["STATE"] == "A" and val["OFFER_ID"] == "3001"  # 非敏感列原样
    assert it["is_sensitive"] == 1
    assert it["key_path"] == "CCRM3.UPC.PM_OFFER_CHA.OFFER_ID=3001,CHA_ID=MZA"
    assert it["value_fingerprint"]   # 敏感行有 HMAC 指纹(MEDIUM 修)


def test_snapshot_small_fingerprint_distinguishes_same_suffix():
    # 后4位相同的两个不同密码 -> mask 相同但 fingerprint 不同(diff 不漏报)
    f1 = snapshot_small([{"ID": "1", "PASSWORD": "aaaa9999"}], ["ID"], "D", "O", "T", ["password"], b"s")[0]
    f2 = snapshot_small([{"ID": "1", "PASSWORD": "bbbb9999"}], ["ID"], "D", "O", "T", ["password"], b"s")[0]
    assert json.loads(f1["value_raw"])["PASSWORD"] == json.loads(f2["value_raw"])["PASSWORD"]  # mask 同
    assert f1["value_fingerprint"] != f2["value_fingerprint"]                                  # 指纹不同


def test_snapshot_big_enumerates_counts():
    group_rows = [{"WHITE_TYPE": "A", "CNT": 1200}, {"WHITE_TYPE": "B", "CNT": 30}]
    items = snapshot_big(group_rows, key_col="WHITE_TYPE", db="CCRM3", owner="UPC", table="CB_WL")
    paths = {i["key_path"] for i in items}
    assert "CCRM3.UPC.CB_WL.WHITE_TYPE.A" in paths
    assert any(i["key_path"].endswith("_summary") for i in items)


def test_table_to_code_via_05_store():
    # MEDIUM 4: 表->代码经 05 lineage_evidence(source_path); .sql 无 container -> source_file 级
    from contextos.lineage import store as L
    eng = create_engine("sqlite:///:memory:")
    L.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(insert(L.lineage_edges).values(edge_id="E1", src_table="PM_OFFER_CHA", dst_table="X"))
        c.execute(insert(L.lineage_evidence).values(edge_id="E1", evidence_ref="order/impl/PmOfferDao.java:42"))
    refs = table_to_code(eng, table="PM_OFFER_CHA")
    assert any("PmOfferDao.java" in r["source_file"] for r in refs)
