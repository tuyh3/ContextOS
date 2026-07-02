# contextos/config_dim/tests/test_sensitive.py
from contextos.config_dim.sensitive import (
    is_sensitive_key, mask_value, value_fingerprint, sanitize_text, sanitize_item_value,
)

_PATTERNS = ["password", "passwd", "secret", "token", "credential"]


def test_detect_sensitive_key():
    assert is_sensitive_key("jdbc.password", _PATTERNS)
    assert is_sensitive_key("app.api_secret", _PATTERNS)
    assert not is_sensitive_key("jdbc.url", _PATTERNS)
    assert not is_sensitive_key("primary_key", _PATTERNS)  # 'key' 不在默认 patterns -> 不误判


def test_mask_value_keeps_last4():
    assert mask_value("supersecret3f7a") == "****3f7a"
    assert mask_value("ab") == "****"   # 短值全掩


def test_fingerprint_differs_same_suffix():
    salt = b"local-salt"
    a = value_fingerprint("pwAAAA1234", salt)
    b = value_fingerprint("pwBBBB1234", salt)  # 后4位同
    assert a != b and len(a) == 64  # HMAC-SHA256 hex


def test_sanitize_text_redacts_secret_in_free_text():
    # excerpt 自由文本: jdbc.password=xxx 这行的值要被 redact
    line = 'jdbc.password=supersecret3f7a  # prod'
    out = sanitize_text(line, sensitive_patterns=_PATTERNS)
    assert "supersecret3f7a" not in out
    assert "jdbc.password" in out  # key 保留, 值打码


def test_sanitize_text_redacts_kv_inside_string_literal():
    # 盲区修复(2026-06-11): 敏感 kv 整体藏在字符串字面量里且外层变量名不敏感(Java 源码形态)。
    # 旧行为: _KV_RE 先匹配外层 s = "..."(key=s 不命中 patterns)把引号段整体消费,
    # 引号内的 password=... 永远扫不到 -> Plan 04b read_symbol 输出面残余暴露窗口。
    line = 'String s = "password=supersecret3f7a";'
    out = sanitize_text(line, sensitive_patterns=_PATTERNS)
    assert "supersecret3f7a" not in out
    assert "password" in out          # key 保留, 值打码
    assert 'String s = "' in out      # 外层结构(变量名/引号)保留


def test_sanitize_text_redacts_nested_kv_in_unquoted_value():
    # 同类盲区的无引号形态: 贪婪 val([^\s#;,]+)把嵌套 kv 整段吃掉
    line = "s=password=supersecret3f7a"
    out = sanitize_text(line, sensitive_patterns=_PATTERNS)
    assert "supersecret3f7a" not in out


def test_sanitize_text_deep_kv_chain_no_crash():
    # 递归深度护栏: & 不在 val 停止符内, 长 query string 每层递归剥一个参数,
    # 无护栏时数百参数即 RecursionError 崩掉 sanitizer chokepoint(输出路径不许崩)
    line = "url=" + "&".join(f"k{i}=v{i}" for i in range(2000))
    out = sanitize_text(line, sensitive_patterns=_PATTERNS)
    assert "k1999=v1999" in out   # 非敏感内容保留, 且没崩


def test_sanitize_text_string_literal_without_secret_unchanged():
    # 红线 #9 语义: 打码凭据不打码拓扑 -- 引号内无敏感 kv 的字面量逐字保留
    line = 'String url = "jdbc:oracle:thin:@h:1521/svc";'
    assert sanitize_text(line, sensitive_patterns=_PATTERNS) == line


_SALT = b"local-salt"


def test_oracle_thin_jdbc_creds_masked():
    # review HIGH: Oracle thin URL 内嵌 user/pass(无 ://)必须打码
    v = "jdbc:oracle:thin:scott/tiger@host:1521:SID"
    stored, sens, fp = sanitize_item_value("jdbc.url", v, _PATTERNS, _SALT)
    assert sens == 1 and stored.startswith("****") and fp


def test_ado_connstr_password_masked():
    # review MEDIUM: ADO key=value 连接串(无 ://)含 Password= 必须打码
    v = "Server=h;User Id=sa;Password=pAss"
    stored, sens, fp = sanitize_item_value("connection_string", v, _PATTERNS, _SALT)
    assert sens == 1 and stored.startswith("****")


def test_jdbc_url_no_creds_not_masked_for_backfill():
    # 无内嵌凭据的连接 URL 不打码: owner-backfill(spec §5.2)要读 jdbc.url 取连接身份; host 非凭据
    v = "jdbc:oracle:thin:@172.21.164.201:1521/crmdev1"
    stored, sens, fp = sanitize_item_value("jdbc.url", v, _PATTERNS, _SALT)
    assert sens == 0 and stored == v and fp == ""
    stored2, sens2, _ = sanitize_item_value("app.url", "http://h/svc", _PATTERNS, _SALT)
    assert sens2 == 0 and stored2 == "http://h/svc"
