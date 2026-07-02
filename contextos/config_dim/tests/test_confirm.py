# contextos/config_dim/tests/test_confirm.py
from sqlalchemy import create_engine
from contextos.config_dim.schema import metadata
from contextos.config_dim.confirm import ref_key_for, record_decision, apply_confirmations


def test_ref_key_stable_canonical():
    assert ref_key_for("config_table", owner="UPC", table="PM_OFFER_CHA") == "UPC.PM_OFFER_CHA"
    assert ref_key_for("binding", entity="UPC.T", bind_type="java_class", bind_target="com.x.C") == "UPC.T|java_class|com.x.C"


def test_apply_override_confirm_and_reject():
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    record_decision(eng, customer_id="demoproj", ref_type="config_table", ref_key="UPC.A", decision="confirm", reviewer="u")
    record_decision(eng, customer_id="demoproj", ref_type="config_table", ref_key="UPC.B", decision="reject", reviewer="u")
    cands = [
        {"ref_type": "config_table", "ref_key": "UPC.A", "verdict": "skip"},     # auto skip 但人工 confirm
        {"ref_type": "config_table", "ref_key": "UPC.B", "verdict": "high"},     # auto high 但人工 reject
        {"ref_type": "config_table", "ref_key": "UPC.C", "verdict": "high"},     # 无确认, 原样
    ]
    out = apply_confirmations(eng, "demoproj", cands)
    d = {c["ref_key"]: c for c in out}
    assert d["UPC.A"]["verdict"] == "confirmed"        # 人工 confirm 覆盖 -> 权威
    assert "UPC.B" not in d                              # 人工 reject -> 排除
    assert d["UPC.C"]["verdict"] == "high"              # 无确认 -> 原样


def test_ref_key_survives_rebuild():
    # spec §9 adversarial: ref_key 是 content-derived canonical -> 跨 build(新对象/新内部 id)仍命中确认(HIGH 2)
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    rk = ref_key_for("config_table", owner="UPC", table="PM_OFFER_CHA")
    record_decision(eng, "demoproj", "config_table", rk, "confirm", reviewer="u")
    # build 2: 全新候选(内部 source_id/entity_id 假设都变), ref_key 由 owner.table 重算 -> 同值 -> 仍命中
    fresh = [{"ref_type": "config_table",
              "ref_key": ref_key_for("config_table", owner="UPC", table="PM_OFFER_CHA"),
              "verdict": "skip"}]
    out = apply_confirmations(eng, "demoproj", fresh)
    assert out[0]["verdict"] == "confirmed"
