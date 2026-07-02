from __future__ import annotations

from contextos.rerank.adapters import (
    dimension_for_kind,
    extract_prompt_signals,
    redact_credentials,
)


def test_dimension_routing():
    assert dimension_for_kind("METHOD") == "method"
    assert dimension_for_kind("CLASS") == "method"
    assert dimension_for_kind("API_ENTRY") == "method"   # 入口分类也算 method-like
    assert dimension_for_kind("SQL_TABLE") == "sql"
    assert dimension_for_kind("SQL_TEMPLATE") == "sql"
    assert dimension_for_kind("CONFIG_KEY") == "config"
    assert dimension_for_kind("RULE_SET") == "config"


def test_v2_or_unknown_kind_returns_none():
    assert dimension_for_kind("MENU") is None            # v2 占位, 不投
    assert dimension_for_kind("OTHER") is None
    assert dimension_for_kind("WHATEVER") is None


def test_dimension_routing_breadth():
    """最终 review fast-follow: 覆盖更多 kind, 防 _METHOD_LIKE / KIND_*_DIMENSION 改动悄悄漏掉某 kind。"""
    assert dimension_for_kind("FIELD") == "method"
    assert dimension_for_kind("INTERFACE") == "method"
    assert dimension_for_kind("JOB") == "method"
    assert dimension_for_kind("SQL_COLUMN") == "sql"
    assert dimension_for_kind("CONFIG_FILE") == "config"
    assert dimension_for_kind("CONFIG_TABLE") == "config"
    assert dimension_for_kind("USSD_NODE") is None        # v2 占位
    assert dimension_for_kind("RULE_CLAUSE") is None       # v2 占位


def test_method_allowlist_keeps_safe_fields():
    sig = {"name_match_strength": 1.0, "call_distance_from_seed": 2, "file": "/x.java"}
    out = extract_prompt_signals(sig, "method")
    assert out == {"name_match_strength": 1.0, "call_distance_from_seed": 2}
    assert "file" not in out          # 非白名单字段被丢


def test_sql_allowlist():
    sig = {"relation_type": "INSERT_SELECT", "src": {"table": "A"}, "dst": {"table": "B"},
           "unresolved_reason": None}
    out = extract_prompt_signals(sig, "sql")
    assert out["relation_type"] == "INSERT_SELECT" and "src" in out and "dst" in out
    assert "unresolved_reason" not in out      # 非白名单字段被丢(对称于 method 维)
    assert out["src"] == {"table": "A"}         # 白名单字段嵌套内容原样透传


def test_config_allowlist_keeps_safe_metadata():
    sig = {"entity_key": "offer.switch", "entity_type": "file_key",
           "bind_strategy": "exact_match", "is_sensitive": True}
    out = extract_prompt_signals(sig, "config")
    assert out["entity_key"] == "offer.switch"
    assert out["is_sensitive"] is True


def test_redact_credentials_masks_secrets_keeps_topology():
    """§7 07 层兜底: RAG 摘要里的凭据片段打码, 但无内嵌凭据的 host 拓扑保留(打码凭据不打码拓扑)。"""
    # 凭据被打码
    assert redact_credentials("jdbc.password=supersecret3f7a") == "jdbc.password=****"
    assert redact_credentials("token=abc123") == "token=****"
    assert redact_credentials("api_key: sk-xyz789") == "api_key: ****"
    assert "secret3" not in redact_credentials('"password": "secret3"')   # JSON 引号形不绕过
    assert "secret4" not in redact_credentials("pwd='secret4'")            # 单引号形
    assert redact_credentials("scott/tiger@db") == "****@db"
    assert redact_credentials("user:pass@host") == "****@host"
    assert redact_credentials("://admin:hunter2@h:5432/db") == "://****@h:5432/db"
    # 无凭据的拓扑/业务文本不动
    assert redact_credentials("jdbc:oracle:thin:@dbhost:1521") == "jdbc:oracle:thin:@dbhost:1521"
    assert redact_credentials("PM_OFFER 是套餐主表, 业务域=billing") == "PM_OFFER 是套餐主表, 业务域=billing"
    assert redact_credentials("service at http://api.internal:8080/v1") == "service at http://api.internal:8080/v1"
    # 不漏:任何上述敏感子串都不残留
    blob = redact_credentials("password=p@ss token=tk user:cred@h ://a:b@h")
    for danger in ("p@ss", "token=tk", "cred@", "a:b@"):
        assert danger not in blob


def test_redact_credentials_extended_vocab():
    """audit fuzz 扩词表: Authorization/Bearer/aws/access_key/passphrase/client_secret/refresh_token/cookie。"""
    assert "eyJ" not in redact_credentials("Authorization: Bearer eyJhbG.payload.sig")
    assert "abc123token" not in redact_credentials("use Bearer abc123token here")
    assert "AKIAEXAMPLE" not in redact_credentials("aws_secret_access_key=AKIAEXAMPLE123")
    assert "myphrase9" not in redact_credentials("passphrase=myphrase9")
    assert "cs_live_99" not in redact_credentials("client_secret: cs_live_99")
    assert "rt_abc" not in redact_credentials("refresh_token=rt_abc")
    assert "JSESSIONXYZ" not in redact_credentials("Set-Cookie: token=JSESSIONXYZ")


def test_redact_credentials_no_overmask_on_prose_and_topology():
    """守 fail-safe 反向: 这些『像但不是凭据』的串必须保持原样(防未来有人收紧正则反而误伤 RAG 上下文)。
    若要改 redact, 先保证这些仍 == 原文 —— 它们是 LLM 判 SQL/config 候选要用的拓扑/业务上下文。"""
    for legit in (
        "password policy requires 8 chars",     # 散文 'password' 后非 =/:
        "Bearer of bad news",                    # 'Bearer' 后短词非 token
        "getUserPassword",                       # 标识符
        "db.password.enabled",                   # config key 名(无 =value)
        "http://api.internal:8080/v1",           # 无凭据 URL
        "admin@company.com",                     # email 非凭据
        "jdbc:oracle:thin:@dbhost:1521",         # host 拓扑无内嵌凭据
    ):
        assert redact_credentials(legit) == legit, f"over-mask 了合法串: {legit!r}"


def test_sensitive_chokepoint_drops_raw_value():
    """敏感值脱敏 + 07 §7: 配置原始值 / 快照 / DB 行数据绝不进 prompt(即便上游吐了)。"""
    leaky = {
        "entity_key": "spring.datasource.url",
        "bind_strategy": "exact_match",
        "value_raw": "jdbc:oracle:thin:scott/TIGER@db",   # 凭据
        "value": "secret-token-abc123",
        "db_snapshot": [{"PASSWORD": "p@ss"}],
        "excerpt": "password=p@ss",
        "rows": [["secret"]],
    }
    out = extract_prompt_signals(leaky, "config")
    blob = repr(out)
    for danger in ("value_raw", "TIGER", "secret-token-abc123", "p@ss", "secret"):
        assert danger not in blob, f"敏感值泄漏: {danger}"
    # 只保留安全 key
    assert out == {"entity_key": "spring.datasource.url", "bind_strategy": "exact_match"}
