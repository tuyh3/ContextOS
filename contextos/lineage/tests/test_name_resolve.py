"""Layer 7 NameResolver 测试。离线(空元数据)+ 在线(有元数据)两路。"""
from contextos.profile.schema import ShardStrategy, TablesConfig
from contextos.storage.db import make_engine
from contextos.lineage import store


def _eng():
    e = make_engine("sqlite://")
    store.create_all(e)
    return e


def test_offline_profile_only_normalization():
    """空元数据: 只做 Profile 归一, db/owner 空, has_metadata=False。"""
    from contextos.lineage.name_resolve import NameResolver
    eng = _eng()
    cfg = TablesConfig(monthly_pattern=r"_\d{6}$",
                       typo_map={"CSPF2": "CSFP2"})
    r = NameResolver(eng, cfg)
    assert r.has_metadata is False
    # 月表归并: AM_BILL_202403 -> AM_BILL(模板)
    db, owner, tpl, dtype = r.resolve_table("AM_BILL_202403")
    assert tpl == "AM_BILL"
    assert db == "" and owner == ""
    # typo 修正
    _d, _o, tpl2, _t = r.resolve_table("CSPF2")
    assert tpl2 == "CSFP2"
    # exclude_schemas 命中 -> 返回空模板(调用方跳过)
    _d, _o, tpl3, _t = r.resolve_table("FOO", schema="SYS")
    assert tpl3 == ""


def test_shard_strategy_merge():
    from contextos.lineage.name_resolve import NameResolver
    eng = _eng()
    cfg = TablesConfig(shard_strategy=ShardStrategy(type="regex", pattern=r"_0?9\d{2}$"))
    r = NameResolver(eng, cfg)
    _d, _o, tpl, _t = r.resolve_table("OM_LINE_0921")
    assert tpl == "OM_LINE"


def test_online_metadata_owner_and_view():
    """有元数据: owner 推断 + view 识别 + table_exists。"""
    from contextos.lineage.name_resolve import NameResolver
    eng = _eng()
    store.write_table_metadata(eng, [
        dict(template_name="PM_OFFER_CHA", db_name="CCRM3", owner="UPC",
             comment="Offer 渠道授权表", dataset_type="TABLE"),
        dict(template_name="V_OFFER", db_name="CCRM3", owner="UPC",
             comment="", dataset_type="VIEW"),
    ])
    r = NameResolver(eng, TablesConfig())
    assert r.has_metadata is True
    db, owner, tpl, dtype = r.resolve_table("PM_OFFER_CHA")
    assert (db, owner, tpl, dtype) == ("CCRM3", "UPC", "PM_OFFER_CHA", "TABLE")
    assert r.table_exists("PM_OFFER_CHA") is True
    assert r.table_exists("NOPE") is False
    _d, _o, _t, dtype2 = r.resolve_table("V_OFFER")
    assert dtype2 == "VIEW"


def test_multi_owner_same_table_name_resolves_ambiguous():
    """同名表跨 owner: table_exists=True; 无 schema 提示时 owner 留空(诚实, 不乱猜)。

    回归 review Finding #1 边消歧: 边裸名匹配多 owner -> 不能静默挑一个; owner/db 留空,
    交给 Plan 06 datasource 回填(见 2026-06-03 衔接 doc §4)。schema 提示则按 schema 命中。"""
    from contextos.lineage.name_resolve import NameResolver
    eng = _eng()
    store.write_table_metadata(eng, [
        dict(template_name="COMMON_T", db_name="DB1", owner="UPC", comment="客户公共表",
             dataset_type="TABLE"),
        dict(template_name="COMMON_T", db_name="DB2", owner="SEC", comment="权限公共表",
             dataset_type="TABLE"),
    ])
    r = NameResolver(eng, TablesConfig())
    assert r.table_exists("COMMON_T") is True
    # 无 schema: 多 owner 歧义 -> owner/db 留空, template 仍归一出 COMMON_T
    db, owner, tpl, _dt = r.resolve_table("COMMON_T")
    assert tpl == "COMMON_T"
    assert owner == "" and db == ""
    # 有 schema 提示: 命中对应 owner
    db2, owner2, _t2, _dt2 = r.resolve_table("COMMON_T", schema="SEC")
    assert owner2 == "SEC" and db2 == "DB2"


def test_schema_hint_mismatch_does_not_choose_other_owner():
    """显式 schema 未命中 metadata 时, 不借别的 owner 的唯一行(review 三轮 HIGH)。

    resolve_table('T_DST', schema='SEC') 而 metadata 只有 UPC.T_DST -> 不能返回 owner=UPC
    (那把 SEC.T_DST 错标成 UPC.T_DST 身份, 比留空更危险); 显式 schema 权威 -> owner=SEC, db=''。"""
    from contextos.lineage.name_resolve import NameResolver
    eng = _eng()
    store.write_table_metadata(eng, [
        dict(template_name="T_DST", db_name="DB1", owner="UPC", comment="", dataset_type="TABLE")])
    r = NameResolver(eng, TablesConfig())
    db, owner, tpl, _dt = r.resolve_table("T_DST", schema="SEC")
    assert owner == "SEC"            # 信 SQL 的 schema, 不借 UPC
    assert db == ""                  # 无 SEC.T_DST 元数据 -> db 留空, 不借 UPC 的 DB1
    assert tpl == "T_DST"
    # schema 命中时仍正常富化(不误伤)
    db2, owner2, _t, _d = r.resolve_table("T_DST", schema="UPC")
    assert (owner2, db2) == ("UPC", "DB1")
    # 无 schema 提示 + 单 owner -> 仍用 metadata 的 owner(不误伤既有行为)
    db3, owner3, _t3, _d3 = r.resolve_table("T_DST")
    assert (owner3, db3) == ("UPC", "DB1")


def test_online_synonym_expansion():
    from contextos.lineage.name_resolve import NameResolver
    eng = _eng()
    store.write_table_metadata(eng, [
        dict(template_name="REAL_TAB", db_name="CCRM3", owner="UPC",
             comment="", dataset_type="TABLE")])
    store.write_table_synonyms(eng, [
        dict(synonym_name="SYN_TAB", db_name="CCRM3", table_owner="UPC",
             table_name="REAL_TAB", db_link="")])
    r = NameResolver(eng, TablesConfig())
    _d, _o, tpl, _t = r.resolve_table("SYN_TAB")
    assert tpl == "REAL_TAB"
