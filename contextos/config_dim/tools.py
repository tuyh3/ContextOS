"""06 维证据 tool 函数(Plan 10 Task 5)。

MCP/CLI 共用的原子查询薄层: 查已 build 的配置维表(config_items / config_entities /
config_bindings / rule_sets / rule_bindings / config_snapshots),全返回纯 dict/list,
**不碰 MCP 协议**(那是 mcp_server/tools/* 的 Task 7),CLI/Python lib 直接复用本层。

安全(必守)
----------
- 所有返回给上层的**自由文本字段**(excerpt / description / evidence / value_raw)统一过
  sensitive.sanitize_text(text, patterns),**绝不**让明文密码/token/连接串泄漏到 MCP host。
  value_raw 落盘时 06 build 已对敏感值掩码(sanitize_item_value),这里再过一道 sanitize_text
  做纵深防御(防"落盘那刻没判敏感、但自由文本里嵌了凭据"漏网)。
- diff_config 缺一侧 config_snapshots 真数据 -> 优雅降级返 {note:'snapshot_missing', ...},
  绝不抛(多环境真快照 v1 常缺,见 design 决策11 / spec caveat)。
- 查询全走 SQLAlchemy select 查配置表,**不**新发 Oracle(配置值已物化在 config_items 里)。

复用: schema(config_dim/schema.py 12 表)、sensitive.sanitize_text(config_dim/sensitive.py)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from contextos.config_dim import schema
from contextos.config_dim.sensitive import redact_secrets_in_text, sanitize_text

_SNAPSHOT_MISSING = "snapshot_missing"

# 安全floor(纵深防御): 即便调用方忘传 patterns(或传空), 这些通用敏感 key 仍强制 redact。
# 镜像 profile.config.sensitive_key_patterns 默认值(profile/schema.py ConfigConfig);
# host 不可信(红线#9), MCP 输出绝不能因 caller 漏配而泄漏明文凭据。caller 传的客户专属
# patterns 与本 floor 取并集, 不互相覆盖。
_DEFAULT_SENSITIVE_PATTERNS = ("password", "passwd", "secret", "token", "credential")


def _redact(text: Any, patterns: list[str] | None) -> str:
    """自由文本字段统一脱敏入口: None/非 str 归一为 str 后过 sanitize_text。

    两层(WF2 security finding 修复): (1) sanitize_text redact 命中敏感 key 的 value 段
    (key=value 行); (2) redact_secrets_in_text 补 sanitize_text 漏的**内嵌凭据连接串**
    (jdbc user/pass@、://user:pass@)和**裸 secret token**(sk-/ghp_/AKIA),保留拓扑。
    **floor**: caller patterns 与 _DEFAULT_SENSITIVE_PATTERNS 取并集 —— 即便 caller 传空,
    通用凭据 key + 内嵌凭据/裸 token 仍强制打码(红线#9 host 不可信, 不靠 caller 自觉)。
    """
    merged: list[str] = list(_DEFAULT_SENSITIVE_PATTERNS)
    for p in (patterns or []):
        if p not in merged:
            merged.append(p)
    s = sanitize_text("" if text is None else str(text), merged)
    return redact_secrets_in_text(s)        # 补内嵌凭据连接串 + 裸 token


# --------------------------------------------------------------------------- lookup_config


def lookup_config(engine: Engine, *, config_key: str, patterns: list[str],
                  salt: bytes) -> dict[str, Any]:
    """config_items + config_entities 双路: 先按 config_key 精确, miss 则 key_path 子串。

    value_raw 落盘已是脱敏值, excerpt/description 等自由文本再过 sanitize_text(纵深防御)。
    返回 {config_key, items:[{item_id, config_key, key_path, value_raw, value_type,
    is_sensitive, description}], entity:{entity_id, entity_key, entity_type, description}|None,
    sources:[source_id...]}。空 config_key / 无命中 -> items=[]、entity=None。

    salt 参数为契约对齐(MCP 包装层从 load_or_create_salt 取); value_raw 已在 build 期用 salt
    掩码,本读取层不再重算 fingerprint,故 salt 当前不参与计算(保留为未来 fingerprint 校验位)。
    """
    items_tbl = schema.config_items
    ents_tbl = schema.config_entities

    result: dict[str, Any] = {"config_key": config_key, "items": [],
                              "entity": None, "sources": []}
    if not config_key:
        return result

    with engine.connect() as conn:
        # 1) exact config_key 命中
        rows = conn.execute(
            select(items_tbl).where(items_tbl.c.config_key == config_key)
        ).fetchall()
        # 2) miss -> key_path 子串 fallback
        if not rows:
            rows = conn.execute(
                select(items_tbl).where(items_tbl.c.key_path.contains(config_key))
            ).fetchall()

        source_ids: list[str] = []
        entity_ids: list[str] = []
        for r in rows:
            m = r._mapping
            sid = m["source_id"]
            eid = m["entity_id"]
            if sid and sid not in source_ids:
                source_ids.append(sid)
            if eid and eid not in entity_ids:
                entity_ids.append(eid)
            result["items"].append({
                "item_id": m["item_id"],
                "config_key": m["config_key"],
                "key_path": m["key_path"],
                # value_raw 落盘已脱敏, 再过 sanitize_text 防自由文本嵌凭据漏网
                "value_raw": _redact(m["value_raw"], patterns),
                "value_type": m["value_type"],
                "is_sensitive": m["is_sensitive"],
                "description": _redact(m["description"], patterns),
            })
        result["sources"] = source_ids

        # entity: 取命中 items 关联的第一个 entity(双路里 config_key 通常对一个 entity)
        if entity_ids:
            ent = conn.execute(
                select(ents_tbl).where(ents_tbl.c.entity_id == entity_ids[0])
            ).fetchone()
            if ent is not None:
                em = ent._mapping
                result["entity"] = {
                    "entity_id": em["entity_id"],
                    "entity_key": em["entity_key"],
                    "entity_type": em["entity_type"],
                    "description": _redact(em["description"], patterns),
                }
    return result


# --------------------------------------------------------------------------- lookup_rule


def lookup_rule(engine: Engine, *, rule_set: str,
                patterns: list[str] | None = None) -> dict[str, Any]:
    """rule_sets 按 name 或 rule_set_id 命中 + rule_bindings(Scope A)。

    返回 {rule_set, rule_set_id, category, owner_domain, status, bindings:[{bind_type,
    bind_target, bind_role, evidence}]}。无命中 -> category/owner_domain='' + bindings=[]。
    bindings.evidence 自由文本过 sanitize(纵深防御)。
    """
    pats = patterns or []
    rs_tbl = schema.rule_sets
    rb_tbl = schema.rule_bindings

    result: dict[str, Any] = {"rule_set": rule_set, "rule_set_id": "",
                              "category": "", "owner_domain": "", "status": "",
                              "bindings": []}
    if not rule_set:
        return result

    with engine.connect() as conn:
        rs = conn.execute(
            select(rs_tbl).where(
                (rs_tbl.c.rule_set_id == rule_set) | (rs_tbl.c.name == rule_set)
            )
        ).fetchone()
        if rs is None:
            return result
        rm = rs._mapping
        rsid = rm["rule_set_id"]
        result.update({
            "rule_set": rm["name"],
            "rule_set_id": rsid,
            "category": rm["category"] or "",
            "owner_domain": rm["owner_domain"] or "",
            "status": rm["status"] or "",
        })
        for b in conn.execute(
            select(rb_tbl).where(rb_tbl.c.rule_set_id == rsid)
        ):
            bm = b._mapping
            result["bindings"].append({
                "bind_type": bm["bind_type"],
                "bind_target": bm["bind_target"],
                "bind_role": bm["bind_role"],
                "evidence": _redact(bm["evidence"], pats),
            })
    return result


# --------------------------------------------------------------------------- trace_config_impact


def trace_config_impact(engine: Engine, *, entity_key: str,
                        patterns: list[str] | None = None) -> dict[str, Any]:
    """配置 entity -> direct_bindings(class/method/table)。复用 config_bindings 查询。

    v1 不做 caller BFS(transitive 调用链是 v2)。返回 {entity_key, direct_bindings:[{bind_type,
    bind_target, bind_direction, bind_strategy, confidence, evidence}]}。无命中 entity -> [].
    bindings.evidence 自由文本过 sanitize。
    """
    pats = patterns or []
    ents_tbl = schema.config_entities
    cb_tbl = schema.config_bindings

    result: dict[str, Any] = {"entity_key": entity_key, "direct_bindings": []}
    if not entity_key:
        return result

    with engine.connect() as conn:
        ents = conn.execute(
            select(ents_tbl.c.entity_id).where(ents_tbl.c.entity_key == entity_key)
        ).fetchall()
        if not ents:
            return result
        entity_ids = [e._mapping["entity_id"] for e in ents]
        for b in conn.execute(
            select(cb_tbl).where(cb_tbl.c.entity_id.in_(entity_ids))
        ):
            bm = b._mapping
            result["direct_bindings"].append({
                "bind_type": bm["bind_type"],
                "bind_target": bm["bind_target"],
                "bind_direction": bm["bind_direction"],
                "bind_strategy": bm["bind_strategy"],
                "confidence": bm["confidence"],
                "evidence": _redact(bm["evidence"], pats),
            })
    return result


# --------------------------------------------------------------------------- explain_rule_logic


def explain_rule_logic(engine: Engine, *, rule_set_id: str,
                       patterns: list[str] | None = None) -> dict[str, Any]:
    """规则表结构/字段/绑定/示例。返回 {rule_set_id, clauses:[...](Scope A v1 空), bindings:[...],
    sample_columns:[...]}。

    rule_clauses 行级 v1 不 populate(决策11) -> clauses 恒空。sample_columns 取关联 config_source
    的 key_columns(若 db_table 来源)。无命中 rule_set -> bindings=[] + clauses=[]。
    bindings.evidence / clauses 文本过 sanitize。
    """
    pats = patterns or []
    rs_tbl = schema.rule_sets
    rc_tbl = schema.rule_clauses
    rb_tbl = schema.rule_bindings
    src_tbl = schema.config_sources

    result: dict[str, Any] = {"rule_set_id": rule_set_id, "clauses": [],
                              "bindings": [], "sample_columns": []}
    if not rule_set_id:
        return result

    with engine.connect() as conn:
        rs = conn.execute(
            select(rs_tbl).where(rs_tbl.c.rule_set_id == rule_set_id)
        ).fetchone()
        if rs is None:
            return result

        # clauses: Scope B 行级 v1 不填(决策11), 查表但通常空; 在场则文本脱敏
        for cl in conn.execute(
            select(rc_tbl).where(rc_tbl.c.rule_set_id == rule_set_id)
        ):
            cm = cl._mapping
            result["clauses"].append({
                "clause_id": cm["clause_id"],
                "clause_name": _redact(cm["clause_name"], pats),
                "condition_expr": _redact(cm["condition_expr"], pats),
                "action_expr": _redact(cm["action_expr"], pats),
                "status": cm["status"],
            })

        for b in conn.execute(
            select(rb_tbl).where(rb_tbl.c.rule_set_id == rule_set_id)
        ):
            bm = b._mapping
            result["bindings"].append({
                "bind_type": bm["bind_type"],
                "bind_target": bm["bind_target"],
                "bind_role": bm["bind_role"],
                "evidence": _redact(bm["evidence"], pats),
            })

        # sample_columns: 规则表来源(config_source)的 key_columns(JSON 文本) — 给规则字段线索
        source_id = rs._mapping["source_id"]
        if source_id:
            src = conn.execute(
                select(src_tbl.c.key_columns, src_tbl.c.value_columns)
                .where(src_tbl.c.source_id == source_id)
            ).fetchone()
            if src is not None:
                sm = src._mapping
                cols: list[str] = []
                for raw in (sm["key_columns"], sm["value_columns"]):
                    cols.extend(_parse_columns(raw))
                # 去重保序
                seen: set[str] = set()
                result["sample_columns"] = [
                    c for c in cols if not (c in seen or seen.add(c))
                ]
    return result


def _parse_columns(raw: Any) -> list[str]:
    """key_columns/value_columns 落的是 JSON 文本(可能空)。解析失败 -> []。"""
    if not raw:
        return []
    import json
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if isinstance(val, list):
        return [str(x) for x in val]
    return []


# --------------------------------------------------------------------------- diff_config


def diff_config(engine: Engine, *, source_id: str, env_a: str, env_b: str,
                patterns: list[str] | None = None) -> dict[str, Any]:
    """两环境 config_snapshots 下 config_items 的 key 级 diff。

    caveat: 依赖多环境真快照, 缺一侧 -> {note:'snapshot_missing', source_id, env_a/env_b
    存在性}, 绝不抛(design 决策11 / spec caveat)。两侧在场 -> {source_id, env_a, env_b,
    only_in_a:[key...], only_in_b:[key...], changed:{key:{a:val, b:val}}}。
    changed 的 value 若敏感 -> 过 sanitize(纵深防御)。
    """
    pats = patterns or []
    snap_tbl = schema.config_snapshots
    items_tbl = schema.config_items

    with engine.connect() as conn:
        snap_a = conn.execute(
            select(snap_tbl.c.snapshot_id).where(
                (snap_tbl.c.source_id == source_id) & (snap_tbl.c.env == env_a)
            )
        ).fetchone()
        snap_b = conn.execute(
            select(snap_tbl.c.snapshot_id).where(
                (snap_tbl.c.source_id == source_id) & (snap_tbl.c.env == env_b)
            )
        ).fetchone()

        a_exists = snap_a is not None
        b_exists = snap_b is not None
        if not (a_exists and b_exists):
            return {
                "note": _SNAPSHOT_MISSING,
                "source_id": source_id,
                "env_a": {"env": env_a, "exists": a_exists},
                "env_b": {"env": env_b, "exists": b_exists},
            }

        snap_a_id = snap_a._mapping["snapshot_id"]
        snap_b_id = snap_b._mapping["snapshot_id"]

        def _load(snapshot_id: str) -> dict[str, str]:
            out: dict[str, str] = {}
            for r in conn.execute(
                select(items_tbl.c.config_key, items_tbl.c.value_raw)
                .where(items_tbl.c.snapshot_id == snapshot_id)
            ):
                m = r._mapping
                key = m["config_key"]
                if key:
                    out[key] = m["value_raw"] or ""
            return out

        map_a = _load(snap_a_id)
        map_b = _load(snap_b_id)

    keys_a = set(map_a)
    keys_b = set(map_b)
    changed: dict[str, dict[str, str]] = {}
    for k in keys_a & keys_b:
        if map_a[k] != map_b[k]:
            changed[k] = {"a": _redact(map_a[k], pats), "b": _redact(map_b[k], pats)}

    return {
        "source_id": source_id,
        "env_a": env_a,
        "env_b": env_b,
        "only_in_a": sorted(keys_a - keys_b),
        "only_in_b": sorted(keys_b - keys_a),
        "changed": changed,
    }
