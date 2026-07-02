# contextos/config_dim/sensitive.py
"""敏感配置 sanitizer chokepoint(HIGH 1): 所有持久化/展示文本统一过。
detect -> mask(展示) + HMAC fingerprint(diff)。本地 salt 不入 Git。"""
from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

_ENVVAR_RE = re.compile(r"\$\{[^}]+\}")
# key=value 行里抓 value(自由文本 redact 用): 简单 kv + 引号
_KV_RE = re.compile(r"""(?P<key>[\w.\-]+)\s*[=:]\s*(?P<val>"[^"]*"|'[^']*'|[^\s#;,]+)""")

# 凭据出现在 value 里 -> 敏感(与 key 无关; review final HIGH/MEDIUM 修):
#  (1) ADO/连接串 password=/pwd=/secret=...  (2) user/pass@host(Oracle thin 内嵌凭据)
#  (3) scheme://user:pass@host
# 关键: **无内嵌凭据的连接 URL**(jdbc:oracle:thin:@host)不在此 -> 不打码。因 owner-backfill
# (spec §5.2 overlay)要读 jdbc.url 取连接身份; host/instance 是基建非凭据。"打码凭据不打码拓扑"。
_CREDS_IN_VALUE = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|credential)\s*=\s*[^\s;]+"
    r"|[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+@"
    r"|://[^/\s@]+:[^/\s@]+@"
)


def is_sensitive_key(key: str, sensitive_patterns: list[str]) -> bool:
    k = (key or "").lower()
    if any(p in k for p in sensitive_patterns):
        # 排除 primary_key/foreign_key 这类 'key' 误命中(默认 patterns 不含裸 'key', 双保险)
        if k.endswith("_key") and "secret" not in k and "token" not in k:
            return False
        return True
    return False


def is_sensitive_value(value: str) -> bool:
    v = value or ""
    return bool(_ENVVAR_RE.search(v)) or bool(_CREDS_IN_VALUE.search(v))


def mask_value(value: str) -> str:
    v = value or ""
    return f"****{v[-4:]}" if len(v) >= 5 else "****"


def value_fingerprint(value: str, salt: bytes) -> str:
    return hmac.new(salt, (value or "").encode("utf-8"), hashlib.sha256).hexdigest()


# 递归重扫深度护栏: 真实嵌套形态(字符串字面量/贪婪 val)只有 1-2 层; & 不在 _KV_RE
# val 停止符内, 长 query string 每层递归剥一个参数, 无护栏数百参数即 RecursionError
# 崩掉 chokepoint。超过 cap 退回扫描前原文(= 修复前行为, 不更差)。
_SANITIZE_MAX_DEPTH = 10


def sanitize_text(text: str, sensitive_patterns: list[str]) -> str:
    """自由文本(excerpt/snippet)redact: 命中敏感 key 的 value 段打码。

    value 段本身可能再藏 key=value: 字符串字面量 `String s = "pin=9999";`(外层
    key=s 不敏感, 引号段被 _KV_RE 整体消费后内层永远扫不到 -- Plan 04b read_symbol
    输出面实测盲区)或贪婪无引号 `s=pin=9999`。外层 key 不敏感时对 value 内容递归
    重扫(每层剥掉 key+分隔符, 严格变短; 深度受 _SANITIZE_MAX_DEPTH 护栏); 内层
    无命中则逐字保留原文。
    """
    def _sub(s: str, depth: int) -> str:
        def _repl(m: re.Match) -> str:
            key, val = m.group("key"), m.group("val")
            if is_sensitive_key(key, sensitive_patterns):
                return f"{key}={mask_value(val.strip(chr(34) + chr(39)))}"
            if depth >= _SANITIZE_MAX_DEPTH:
                return m.group(0)
            quote = val[0] if val[:1] in ('"', "'") else ""
            inner = val[1:-1] if quote else val
            new_inner = _sub(inner, depth + 1)
            if new_inner == inner:
                return m.group(0)
            prefix = m.group(0)[: m.start("val") - m.start(0)]
            return f"{prefix}{quote}{new_inner}{quote}"
        return _KV_RE.sub(_repl, s)
    return _sub(text or "", 0)


# well-known secret token 前缀(裸 token, 无 key= 上下文也要 mask; is_sensitive_value 识别不了
# 这类无结构 secret)。前缀锚定避免误伤普通标识符。
_BARE_TOKEN_RE = re.compile(
    r"(?i)\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{8,}"       # OpenAI sk- / sk-proj-
    r"|gh[posru]_[A-Za-z0-9]{20,}"                    # GitHub ghp_/gho_/...
    r"|AKIA[A-Z0-9]{12,}"                             # AWS access key id
    r"|xox[baprs]-[A-Za-z0-9-]{10,})"                 # Slack xoxb-/xoxp-/...
)


def _mask_cred_fragment(frag: str) -> str:
    """mask _CREDS_IN_VALUE 命中的凭据片段, 保留结构 token(://、@、key=)和 @ 后拓扑。"""
    if frag.startswith("://"):
        return "://****@"                  # scheme://user:pass@ -> scheme://****@
    if "=" in frag:
        return frag.split("=", 1)[0] + "=****"   # password=xxx -> password=****
    return "****@"                          # user/pass@ -> ****@(@ 后 host 保留)


def redact_secrets_in_text(text: str) -> str:
    """mask 自由文本里的**内嵌凭据连接串**(user/pass@、://user:pass@、password=)和**裸 secret
    token**(sk-/ghp_/AKIA/xox), 保留拓扑(host/instance 非凭据)。

    与 sanitize_text 互补: 后者只抓 key=value 形状, 漏掉裸连接串/token。本函数是输出给**不可信
    MCP host**(红线#9)前对自由文本的最后一道防线(description 等 build 期不脱敏字段的唯一防线)。
    'jdbc:oracle:thin:@host'(无内嵌凭据)不打码 —— 打码凭据不打码拓扑(owner-backfill 要读连接身份)。
    """
    t = text or ""
    t = _CREDS_IN_VALUE.sub(lambda m: _mask_cred_fragment(m.group(0)), t)
    t = _BARE_TOKEN_RE.sub(lambda m: mask_value(m.group(0)), t)
    return t


def load_or_create_salt(cache_dir: Path) -> bytes:
    """本地 salt: cache/.config_salt, 随机生成, 不入 Git(丢失只是下次 diff 认为全变)。"""
    f = Path(cache_dir) / ".config_salt"
    if f.exists():
        return f.read_bytes()
    import os
    salt = os.urandom(32)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(salt)
    return salt


def sanitize_item_value(key: str, value: str, sensitive_patterns: list[str], salt: bytes):
    """落 config_items 用: 返 (value_raw_to_store, is_sensitive, value_fingerprint)。"""
    sensitive = is_sensitive_key(key, sensitive_patterns) or is_sensitive_value(value)
    if sensitive:
        return mask_value(value), 1, value_fingerprint(value, salt)
    return value, 0, ""
