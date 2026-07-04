"""06 维证据 tool 函数纯逻辑测试(Plan 10 Task 5)。

设计思路
--------
config_dim/tools.py 是 MCP/CLI 共用的"查已 build 的配置表(config_items/config_entities/
config_bindings/rule_sets/rule_bindings/config_snapshots),返回纯 dict"薄层,**不碰 MCP 协议**,
**不新发 Oracle**(配置值已物化在表里)。本测试覆盖:

1. 命中路径:每个函数对种入的中性配置行返回正确 schema。
2. 空/降级路径:miss 返回结构完整 dict(空 list / note 标记),绝不抛;diff_config 缺一侧
   config_snapshots 真数据时返 {note:'snapshot_missing', ...}。
3. 脱敏覆盖(安全红线):所有返回给上层的自由文本字段(excerpt/description/evidence/value_raw)
   过 sensitive.sanitize_text,fixture 塞含敏感值的 description/excerpt(password=secret123),
   断言 tool 输出里该值被 mask,不泄漏明文。
4. fresh 环境 fail-clean(2026-07-04 用户裁决):config 维表族未建(如只跑过 init --only
   code 的干净库)时,5 个工具一律抛 ConfigDimNotBuilt——message 可行动(提示跑 contextos
   init)且不含裸 SQL / 内部表名(经 ToolError 直出不可信 host,红线 #9 卫生)。**不是**
   空降级:config 唯一数据源就是已 build 索引,返回"空配置"会被 host 误读为"配置项不存在"
   (对照 lineage 家族的空降级修法——那边有 Oracle live 第二数据源 + oracle_offline 绝不抛
   契约,config 不适用)。守卫在函数最顶部,空参短路也不例外(统一语义)。
   与场景 2 的边界:snapshot_missing 是"表在、缺快照行",fail-clean 是"表不存在",两者不互改。

评分标准
--------
- lookup_config: exact 命中 config_key + miss 子串 + 敏感 description redact。
- lookup_rule: name/id 命中 + rule_bindings 带出 + 空结果。
- trace_config_impact: entity_key -> direct_bindings(不含 caller BFS)+ 空结果。
- explain_rule_logic: rule_set_id -> clauses(Scope A 可空)/bindings/sample_columns + 空。
- diff_config: 双环境 key 级 diff + 缺快照降级 note='snapshot_missing'。
- 脱敏:每个出自由文本的函数,敏感明文不出现在输出任何字符串里。
- fresh 库:5 工具逐个抛 ConfigDimNotBuilt,message 含 'contextos init'、不含 'no such
  table'/'SELECT'/任何内部表名;已建库行为零变化(上面 1-3 全量继续绿)。

测试 fixture 用中性合成名(feature.flag.x / application.properties / APP / ORDERS),不掺真
客户 schema/owner/表名(守 feedback_offline_test_neutral_fixtures)。

自动脚本测试逻辑
----------------
内存 SQLite + schema.metadata.create_all 建配置维 12 表,_seed 灌中性 config_sources/
config_entities/config_items/config_bindings/rule_sets/rule_bindings 行。salt 用固定 32B
合成值,patterns 用通用敏感词表。查询全走 SQLAlchemy select,无 Oracle。
"""
from __future__ import annotations

from sqlalchemy import create_engine

from contextos.config_dim import schema, tools

_PATTERNS = ["password", "passwd", "secret", "token", "credential"]
_SALT = b"x" * 32


# --------------------------------------------------------------------------- fixtures


def _seed(engine) -> None:
    """中性合成配置维数据。名一律合成(feature.flag.x / APP.ORDERS),不含真客户 schema。

    刻意塞两处敏感明文(config_items.description 含 'password=secret123' /
    config_evidence.excerpt 同)以验证脱敏覆盖。
    """
    schema.metadata.create_all(engine)
    with engine.begin() as c:
        # 多行 insert(executemany): 同一 list 内每 dict 必须同 key 集(SQLAlchemy 不逐行套
        # column default)。两源给齐全字段, 缺的填空串保持 key 一致。
        c.execute(schema.config_sources.insert(), [
            {"source_id": "s1", "source_type": "file",
             "file_path": "application.properties", "db_name": "", "owner": "",
             "table_name": "", "module": "app", "description": "app config file"},
            {"source_id": "s2", "source_type": "db_table", "file_path": "",
             "db_name": "APP", "owner": "APP", "table_name": "ORDERS",
             "module": "order", "description": "order config table"},
        ])
        c.execute(schema.config_entities.insert(), [
            {"entity_id": "en1", "source_id": "s1", "entity_key": "feature.flag.x",
             "entity_type": "file_key", "description": "feature flag x"},
        ])
        c.execute(schema.config_items.insert(), [
            {"item_id": "i1", "source_id": "s1", "entity_id": "en1",
             "snapshot_id": "snap1", "config_key": "feature.flag.x",
             "key_path": "feature.flag.x", "value_raw": "true", "value_type": "bool",
             "is_sensitive": 0,
             # 敏感明文埋进自由文本 description -> tool 输出必须 redact
             "description": "toggle; set password=secret123 to enable"},
        ])
        c.execute(schema.config_bindings.insert(), [
            {"binding_id": "b1", "entity_id": "en1", "bind_type": "java_class",
             "bind_target": "com.x.FeatureConfig", "bind_strategy": "exact_match",
             "bind_direction": "read", "confidence": "high",
             "evidence": "annotation@F.java:10 token=secret123"},
        ])
        c.execute(schema.rule_sets.insert(), [
            {"rule_set_id": "rs1", "name": "PricingRule", "source_id": "s2",
             "category": "pricing", "owner_domain": "billing", "status": "active",
             "description": "pricing rule set"},
        ])
        c.execute(schema.rule_bindings.insert(), [
            {"binding_id": "rb1", "rule_set_id": "rs1", "bind_type": "source_file",
             "bind_target": "PricingSvc.java", "bind_role": "subject",
             "evidence": "table_to_code"},
        ])


# --------------------------------------------------------------------------- lookup_config


def test_lookup_config_exact_hit():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.lookup_config(e, config_key="feature.flag.x",
                            patterns=_PATTERNS, salt=_SALT)
    assert r["config_key"] == "feature.flag.x"
    assert r["items"]
    assert r["items"][0]["value_raw"] == "true"
    assert r["entity"] is not None
    assert r["entity"]["entity_key"] == "feature.flag.x"
    assert "s1" in r["sources"]


def test_lookup_config_substring_fallback():
    e = create_engine("sqlite://")
    _seed(e)
    # exact miss on 'feature.flag' -> key_path 子串命中 'feature.flag.x'
    r = tools.lookup_config(e, config_key="feature.flag",
                            patterns=_PATTERNS, salt=_SALT)
    assert any(it["config_key"] == "feature.flag.x" for it in r["items"])


def test_lookup_config_miss_empty():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.lookup_config(e, config_key="nonexistent.key.zzz",
                            patterns=_PATTERNS, salt=_SALT)
    assert r["config_key"] == "nonexistent.key.zzz"
    assert r["items"] == []
    assert r["entity"] is None


def test_lookup_config_redacts_sensitive_description():
    """安全: config_items.description 含 password=secret123 -> 输出必须 mask 明文。"""
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.lookup_config(e, config_key="feature.flag.x",
                            patterns=_PATTERNS, salt=_SALT)
    blob = repr(r)
    assert "secret123" not in blob          # 明文不泄漏
    assert r["items"][0]["description"]      # 字段仍在(key 保留, 值打码)
    assert "password" in r["items"][0]["description"]


def test_lookup_config_redacts_embedded_creds_and_bare_tokens():
    """安全(WF2 security finding 修复): _redact 必须 mask 内嵌凭据连接串 + 裸 token,
    不止 key=value 形状。patterns=[] 时 floor 仍强制脱敏(红线#9 host 不可信不靠 caller)。
    保留拓扑(host/instance 非凭据,owner-backfill 要读)——打码凭据不打码拓扑。"""
    e = create_engine("sqlite://")
    schema.metadata.create_all(e)
    with e.begin() as c:
        c.execute(schema.config_entities.insert(), [
            {"entity_id": "enc", "source_id": "sc", "entity_key": "db.conn",
             "entity_type": "file_key",
             # https 内嵌凭据(:// user:pass @)埋自由文本 description
             "description": "primary https://svcuser:LEAKPW123@api.host fallback"}])
        c.execute(schema.config_items.insert(), [
            {"item_id": "ic", "source_id": "sc", "entity_id": "enc",
             "snapshot_id": "snc", "config_key": "db.conn", "key_path": "db.conn",
             # jdbc 内嵌凭据(user/pass@), 非 key=value 形状
             "value_raw": "jdbc:oracle:thin:appuser/SuperPwd9@db01",
             "value_type": "string", "is_sensitive": 0,
             # 裸 secret token, 无 key= 上下文, is_sensitive_value 也识别不了 -> 需前缀检测
             "description": "rotate sk-proj-RAWSECRETONLY weekly"}])
    # patterns=[] 故意空: 验证 floor 强制脱敏, 不靠 caller 传敏感词
    r = tools.lookup_config(e, config_key="db.conn", patterns=[], salt=_SALT)
    blob = repr(r)
    assert "SuperPwd9" not in blob                 # jdbc 内嵌密码不泄漏
    assert "LEAKPW123" not in blob                 # https 内嵌密码不泄漏
    assert "sk-proj-RAWSECRETONLY" not in blob     # 裸 token 不泄漏
    assert "db01" in blob                          # 拓扑保留: jdbc host
    assert "api.host" in blob                      # 拓扑保留: https host


# --------------------------------------------------------------------------- lookup_rule


def test_lookup_rule_hit_by_name():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.lookup_rule(e, rule_set="PricingRule")
    assert r["rule_set"] == "PricingRule"
    assert r["category"] == "pricing"
    assert r["owner_domain"] == "billing"
    assert any(b["bind_target"] == "PricingSvc.java" for b in r["bindings"])


def test_lookup_rule_hit_by_id():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.lookup_rule(e, rule_set="rs1")          # rule_set_id 命中
    assert r["rule_set"] == "PricingRule"
    assert r["bindings"]


def test_lookup_rule_miss_empty():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.lookup_rule(e, rule_set="NoSuchRule")
    assert r["rule_set"] == "NoSuchRule"
    assert r["category"] == ""
    assert r["bindings"] == []


def test_lookup_rule_redacts_evidence():
    """安全: rule_bindings.evidence 自由文本过 sanitize(若含敏感)。"""
    e = create_engine("sqlite://")
    with e.begin() as c:
        schema.metadata.create_all(e)
        c.execute(schema.rule_sets.insert(), [
            {"rule_set_id": "rs9", "name": "R9", "status": "active"}])
        c.execute(schema.rule_bindings.insert(), [
            {"binding_id": "rb9", "rule_set_id": "rs9", "bind_type": "source_file",
             "bind_target": "X.java", "bind_role": "subject",
             "evidence": "ctx password=secret123 here"}])
    r = tools.lookup_rule(e, rule_set="R9")
    assert "secret123" not in repr(r)


# --------------------------------------------------------------------------- trace_config_impact


def test_trace_config_impact_hit_direct_bindings():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.trace_config_impact(e, entity_key="feature.flag.x")
    assert r["entity_key"] == "feature.flag.x"
    assert r["direct_bindings"]
    b0 = r["direct_bindings"][0]
    assert b0["bind_type"] == "java_class"
    assert b0["bind_target"] == "com.x.FeatureConfig"
    assert b0["confidence"] == "high"
    # direct_bindings, v1 不做 caller BFS
    assert "callers" not in b0


def test_trace_config_impact_miss_empty():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.trace_config_impact(e, entity_key="no.such.entity")
    assert r["entity_key"] == "no.such.entity"
    assert r["direct_bindings"] == []


def test_trace_config_impact_redacts_evidence():
    """安全: config_bindings.evidence 自由文本过 sanitize(种入 token=secret123)。"""
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.trace_config_impact(e, entity_key="feature.flag.x")
    assert "secret123" not in repr(r)


# --------------------------------------------------------------------------- explain_rule_logic


def test_explain_rule_logic_hit():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.explain_rule_logic(e, rule_set_id="rs1")
    assert r["rule_set_id"] == "rs1"
    assert r["clauses"] == []          # Scope A: rule_clauses v1 不填(决策11)
    assert any(b["bind_target"] == "PricingSvc.java" for b in r["bindings"])
    assert isinstance(r["sample_columns"], list)


def test_explain_rule_logic_miss_empty():
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.explain_rule_logic(e, rule_set_id="rs_missing")
    assert r["rule_set_id"] == "rs_missing"
    assert r["clauses"] == []
    assert r["bindings"] == []


# --------------------------------------------------------------------------- diff_config


def test_diff_config_missing_snapshot_degrades():
    """无 config_snapshots 真数据 -> 优雅降级,不抛。"""
    e = create_engine("sqlite://")
    _seed(e)
    r = tools.diff_config(e, source_id="s1", env_a="dev", env_b="prod")
    assert r["note"] == "snapshot_missing"
    # 报告两侧存在性供上层判断
    assert r["env_a"]["exists"] is False
    assert r["env_b"]["exists"] is False


def test_diff_config_both_snapshots_key_level_diff():
    """两环境真快照在场 -> key 级 diff(added/removed/changed)。"""
    e = create_engine("sqlite://")
    _seed(e)
    with e.begin() as c:
        c.execute(schema.config_snapshots.insert(), [
            {"snapshot_id": "snapDev", "source_id": "s1", "env": "dev", "is_current": 1},
            {"snapshot_id": "snapProd", "source_id": "s1", "env": "prod", "is_current": 1},
        ])
        # dev: a=1, common=x ; prod: common=y, c=3
        c.execute(schema.config_items.insert(), [
            {"item_id": "d_a", "source_id": "s1", "snapshot_id": "snapDev",
             "config_key": "a", "key_path": "a", "value_raw": "1"},
            {"item_id": "d_common", "source_id": "s1", "snapshot_id": "snapDev",
             "config_key": "common", "key_path": "common", "value_raw": "x"},
            {"item_id": "p_common", "source_id": "s1", "snapshot_id": "snapProd",
             "config_key": "common", "key_path": "common", "value_raw": "y"},
            {"item_id": "p_c", "source_id": "s1", "snapshot_id": "snapProd",
             "config_key": "c", "key_path": "c", "value_raw": "3"},
        ])
    r = tools.diff_config(e, source_id="s1", env_a="dev", env_b="prod")
    assert "note" not in r or r["note"] != "snapshot_missing"
    assert "a" in r["only_in_a"]
    assert "c" in r["only_in_b"]
    assert "common" in r["changed"]


def test_diff_config_redacts_changed_values():
    """安全: changed 值若敏感 -> mask。dev common=password=secret123, prod common=other。"""
    e = create_engine("sqlite://")
    _seed(e)
    with e.begin() as c:
        c.execute(schema.config_snapshots.insert(), [
            {"snapshot_id": "sd", "source_id": "s1", "env": "dev", "is_current": 1},
            {"snapshot_id": "sp", "source_id": "s1", "env": "prod", "is_current": 1},
        ])
        c.execute(schema.config_items.insert(), [
            {"item_id": "x_d", "source_id": "s1", "snapshot_id": "sd",
             "config_key": "db.cfg", "key_path": "db.cfg",
             "value_raw": "password=secret123"},
            {"item_id": "x_p", "source_id": "s1", "snapshot_id": "sp",
             "config_key": "db.cfg", "key_path": "db.cfg",
             "value_raw": "password=other999"},
        ])
    r = tools.diff_config(e, source_id="s1", env_a="dev", env_b="prod")
    assert "secret123" not in repr(r)
    assert "other999" not in repr(r)


# --------------------------------------------------------------------------- fresh env(config 维未建, fail-clean)


def _fresh_engine():
    """模拟只跑过 init --only code 的干净库: engine 可连但 config 维表族整个不存在(不 create_all)。"""
    return create_engine("sqlite://")


def _assert_clean_not_built(excinfo) -> None:
    """fail-clean 消息断言: 可行动(提示跑 init)+ 不泄内部细节(无裸 SQL / 内部表名, 红线 #9)。"""
    msg = str(excinfo.value)
    assert "contextos init" in msg
    for leak in ("no such table", "SELECT", "config_items", "config_entities",
                 "config_bindings", "rule_sets", "rule_bindings", "config_snapshots",
                 "config_sources", "rule_clauses"):
        assert leak not in msg, f"message 泄漏内部细节: {leak!r} in {msg!r}"


def test_lookup_config_fresh_db_raises_clean_not_built():
    import pytest
    with pytest.raises(tools.ConfigDimNotBuilt) as ei:
        tools.lookup_config(_fresh_engine(), config_key="feature.flag.x",
                            patterns=_PATTERNS, salt=_SALT)
    _assert_clean_not_built(ei)


def test_lookup_config_fresh_db_empty_key_also_raises():
    """守卫在函数最顶部: 空参短路也不例外(统一语义, 不给'空结构'半吊子答案)。"""
    import pytest
    with pytest.raises(tools.ConfigDimNotBuilt):
        tools.lookup_config(_fresh_engine(), config_key="", patterns=_PATTERNS, salt=_SALT)


def test_lookup_rule_fresh_db_raises_clean_not_built():
    import pytest
    with pytest.raises(tools.ConfigDimNotBuilt) as ei:
        tools.lookup_rule(_fresh_engine(), rule_set="PricingRule")
    _assert_clean_not_built(ei)


def test_trace_config_impact_fresh_db_raises_clean_not_built():
    import pytest
    with pytest.raises(tools.ConfigDimNotBuilt) as ei:
        tools.trace_config_impact(_fresh_engine(), entity_key="feature.flag.x")
    _assert_clean_not_built(ei)


def test_explain_rule_logic_fresh_db_raises_clean_not_built():
    import pytest
    with pytest.raises(tools.ConfigDimNotBuilt) as ei:
        tools.explain_rule_logic(_fresh_engine(), rule_set_id="rs1")
    _assert_clean_not_built(ei)


def test_diff_config_fresh_db_raises_clean_not_built():
    """表不存在 -> fail-clean 抛; 与 snapshot_missing(表在、缺快照行)是两回事, 后者契约不动。"""
    import pytest
    with pytest.raises(tools.ConfigDimNotBuilt) as ei:
        tools.diff_config(_fresh_engine(), source_id="s1", env_a="dev", env_b="prod")
    _assert_clean_not_built(ei)


def test_lookup_config_partial_tables_still_raises():
    """部分建表态(mutation 探针暴露的缺口): 只建 config_items 缺 config_entities ->
    仍必须抛(any-missing 语义), 绝不静默返回假空结果。"""
    import pytest
    e = _fresh_engine()
    schema.config_items.create(e)
    with pytest.raises(tools.ConfigDimNotBuilt) as ei:
        tools.lookup_config(e, config_key="feature.flag.x",
                            patterns=_PATTERNS, salt=_SALT)
    _assert_clean_not_built(ei)


def test_diff_config_partial_tables_still_raises():
    """只建 config_snapshots 缺 config_items -> 抛 fail-clean, 不裸抛也不假 diff。"""
    import pytest
    e = _fresh_engine()
    schema.config_snapshots.create(e)
    with pytest.raises(tools.ConfigDimNotBuilt) as ei:
        tools.diff_config(e, source_id="s1", env_a="dev", env_b="prod")
    _assert_clean_not_built(ei)
