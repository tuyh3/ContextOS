"""kind -> 维度路由 + 进 prompt 的两道敏感值 chokepoint(07 §7 红线硬执行点)。

两条进 prompt 的路径各有一道兜底:
1. 候选 signals -> `extract_prompt_signals` 白名单(非黑名单):只有显式列出的安全字段能进,
   任何未列字段(尤其 value_raw / value / db_snapshot / excerpt / rows)一律丢 —— 即便上游
   06 将来吐配置原始值也不泄漏。
2. RAG 业务摘要 -> `redact_credentials` 内容级凭据擦洗:RAG snippet 来自 03 语料(物化期 LeakageGate 已
   curate),但 07 组装层再硬筛兜底(§7「任何一层漏了另一层接住」)——业务文档万一含 JDBC
   口令 / token / user:pass@ 也不进外部 LLM。守「打码凭据不打码拓扑」(无内嵌凭据的 host URL 不动)。
"""
from __future__ import annotations

import re
from typing import Any

from contextos.impact_map.enums import KIND_CONFIG_DIMENSION, KIND_SQL_DIMENSION
from contextos.rerank.schema import Dimension

# 凭据片段(与 config_dim.sensitive 同 philosophy: 打码凭据, 不打码拓扑):
#  (1) key=val / key: val 形(password/token/secret/api_key 等)-> 打 val;
#  (2) user/pass@host(Oracle thin 内嵌凭据)与 user:pass@host -> 打凭据段;
#  (3) scheme://user:pass@host -> 打凭据段。
# 无内嵌凭据的 host URL(jdbc:oracle:thin:@host / http://api:8080)不命中 -> 保留。
# 凭据关键词(扩自 audit fuzz: 覆盖常见低误伤形; 与英文散文低碰撞)。
_CRED_KW = (
    r"(?:password|passwd|pwd|passphrase|secret|credential|client[_-]?secret|"
    r"api[_-]?key|access[_-]?key|secret[_-]?key|auth[_-]?token|refresh[_-]?token|token)"
)
_CRED_RE = re.compile(
    # (0) Authorization: <scheme> <tok> / 裸 Bearer <tok> —— 头部形, 打 scheme 后的 token。
    r"(?i)(?P<auth>authorization\s*[:=]\s*(?:bearer|basic|digest|token)\s+)\S+"
    r"|(?P<bearer>bearer\s+)[A-Za-z0-9._\-]{6,}"
    # (1) kv: key["']?[=:]["']? val —— 容 JSON 引号形 "password": "secret" / pwd='x' / password=x
    r"|(?P<kv>" + _CRED_KW + r"[\"']?\s*[=:]\s*[\"']?)(?P<v>[^\s;,\"']+)"
    # (2) user/pass@host(Oracle thin)/ user:pass@host -> 打凭据段
    r"|(?P<up>[A-Za-z0-9_.\-]+[:/][^\s:/@]+@)"
    # (3) scheme://user:pass@host -> 打凭据段
    r"|(?P<url>://)[^/\s@]+:[^/\s@]+@"
)
# 已知残留 gap(与散文不可消歧, 强扩会 over-mask, 故不收 —— 文档化见 design 07 §7):
#  - netrc 空格/tab 分隔 `password <val>`(撞 "password policy")、多值 `password=a,b,c` 尾段 b,c
#    (逗号是 value 边界)、PEM 私钥块、无关键词的纯 base64。这些是 fail-safe 第二层的接受残留,
#    主防线 = 物化期 LeakageGate(正则排除点名改动文件)+ 候选 signals 白名单(已堵 value 通道)。

# 每维允许喂 LLM 的安全字段(白名单)。对齐 07 design §5 表 + 各 provider 实际吐的字段。
_METHOD_FIELDS = (
    "name_match_strength", "call_distance_from_seed", "call_direction",
    "capability_match", "binding_source",
)
_SQL_FIELDS = (
    "relation_type", "lineage_type", "src", "dst", "sql_template_id",
    "evidence_count", "recovery_mode",
)
# config: 全是元数据/类型/标记/标识符,无任何真实配置值。entity_key 是 key 名(标识符,非值)。
# 注意:CONFIG_TABLE 候选(provider 吐 table/resolved_owner/db)无一在白名单内 -> 过滤后 signals
# 为空 {},LLM 只凭 target 名判;这是 plan「已知实现现实 #1」交集为空的预期行为,不是 bug。
_CONFIG_FIELDS = (
    "entity_key", "entity_type", "source_type", "bind_type",
    "bind_target", "bind_strategy", "value_type", "is_sensitive",
)
_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "method": _METHOD_FIELDS, "sql": _SQL_FIELDS, "config": _CONFIG_FIELDS,
}

# v1 可达的"方法类" kind(04 产 METHOD/CLASS/INTERFACE/FIELD + facade resolver 产入口分类)。
# 新增 method-like kind 时与 impact_map.enums.Kind 同步(那是 SSOT,此处是路由子集)。
_METHOD_LIKE = frozenset({
    "METHOD", "CLASS", "INTERFACE", "FIELD", "API_ENTRY", "JOB", "BATCH", "MSG",
})


def dimension_for_kind(kind: str) -> Dimension | None:
    """kind -> 'method'/'sql'/'config';不属三维(v2 占位 MENU/USSD_NODE/RULE_CLAUSE / OTHER /
    未知)返 None,调用方标 status=skipped 不投 —— 别把非方法 kind 硬塞 method prompt 误判。"""
    if kind in KIND_SQL_DIMENSION:
        return "sql"
    if kind in KIND_CONFIG_DIMENSION:
        return "config"
    if kind in _METHOD_LIKE:
        return "method"
    return None


def redact_credentials(text: str) -> str:
    """RAG 摘要 / candidates_block 进 prompt 前的内容级凭据擦洗(§7 07 层兜底)。

    打码 Authorization/Bearer / password=/token=/access_key= / user:pass@ / scheme://user:pass@
    等凭据片段;无内嵌凭据的 host URL(拓扑)保留。redact 偏保守(宁可多打码也不漏)。
    这是 fail-safe 第二层(主防线 = 物化 LeakageGate + 候选 signals 白名单);已知残留 gap 见 _CRED_RE 上方注释。
    """
    def _repl(m: re.Match) -> str:
        if m.group("auth"):
            return m.group("auth") + "****"
        if m.group("bearer"):
            return m.group("bearer") + "****"
        if m.group("kv"):
            return m.group("kv") + "****"
        if m.group("up"):
            return "****@"
        if m.group("url"):
            return "://****@"
        return m.group(0)
    return _CRED_RE.sub(_repl, text or "")


def extract_prompt_signals(signals: dict[str, Any], dim: Dimension) -> dict[str, Any]:
    """白名单过滤:只留该维允许喂 LLM 的安全字段,其余(含敏感原始值)一律丢。

    signals 必须是普通 dict(ProviderCandidate.signals);dim 受 Dimension 约束,
    pyright 在调用点即拦非法维度,故 _ALLOWLIST[dim] 不会 KeyError。
    """
    allow = _ALLOWLIST[dim]
    return {k: signals[k] for k in allow if k in signals}
