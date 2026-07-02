"""DB 行快照(spec §3 决策10 HIGH 1 + design §7 大小分级 + §5.3 MEDIUM 4 表->代码)。

- snapshot_small: 小表(<= 阈值)全量, 每行一条 config_item, row-JSON **按列**掩码。
- snapshot_big:   大表拆条, GROUP BY key_col 的 (value, count) 每枚举值一条 + 一条 _summary。
- table_to_code:  reuse 05 lineage_evidence(source_path) -> 表用在哪些源文件;
                  .sql 无 container -> source_file 级(MEDIUM 4 诚实, container 级后补)。

取数(SELECT * / GROUP BY)走 05 §8.2 execute_query(白名单 + ROWNUM + timeout, 红线#4),
在 C5 pipeline 注入; 本模块只做纯逻辑(rows 已给)。
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.engine import Engine

from contextos.config_dim import sensitive as SENS
from contextos.lineage import store as L


def snapshot_small(rows, pk_cols, db, owner, table, sensitive_patterns, salt) -> list[dict]:
    """小表全量: 每行一条 config_item; row-JSON 按列掩码(HIGH 1 决策10)。"""
    items = []
    for row in rows:
        pk = ",".join(f"{k}={row.get(k)}" for k in pk_cols) if pk_cols else f"ROWID={row.get('ROWID', '')}"
        masked = {}
        row_sensitive = 0
        for col, val in row.items():
            sval = "" if val is None else str(val)
            if SENS.is_sensitive_key(col, sensitive_patterns) or SENS.is_sensitive_value(sval):
                masked[col] = SENS.mask_value(sval)
                row_sensitive = 1
            else:
                masked[col] = sval
        # 敏感行: value_fingerprint = HMAC(salt, 原始整行)(MEDIUM 修: diff 靠指纹, 后4位同也不漏报)
        raw_json = json.dumps(
            {k: ("" if v is None else str(v)) for k, v in row.items()},
            ensure_ascii=False, sort_keys=True)
        fp = SENS.value_fingerprint(raw_json, salt) if row_sensitive else ""
        items.append({
            "key_path": f"{db}.{owner}.{table}.{pk}",
            "config_key": pk, "value_raw": json.dumps(masked, ensure_ascii=False),
            "value_type": "row", "is_sensitive": row_sensitive,
            "value_fingerprint": fp,
        })
    return items


def snapshot_big(group_rows, key_col, db, owner, table) -> list[dict]:
    """大表拆条: GROUP BY key_col 的 (value,count) -> 每枚举值一条 + _summary。
    known-limitation(review LOW): 枚举 key 值不脱敏 —— 假定 GROUP BY 的 key 列非敏感(类型/状态码);
    key 列若恰为敏感维度(脱敏前号码段等)枚举值会明文落 key_path。配置表 key 列极少敏感, 故不脱敏
    (脱敏会破坏 key 身份 -> 无法按 key 查)。"""
    items = []
    total = 0
    for r in group_rows:
        val = r.get(key_col)
        cnt = int(r.get("CNT") or r.get("cnt") or 0)
        total += cnt
        items.append({
            "key_path": f"{db}.{owner}.{table}.{key_col}.{val}",
            "config_key": f"{key_col}={val}", "value_raw": str(cnt),
            "value_type": "count", "is_sensitive": 0,
        })
    items.append({
        "key_path": f"{db}.{owner}.{table}._summary",
        "config_key": "_summary",
        "value_raw": json.dumps({"total_rows": total, "distinct_key_values": len(group_rows)}),
        "value_type": "summary", "is_sensitive": 0,
    })
    return items


def table_to_code(engine_05: Engine, table: str) -> list[dict]:
    """reuse 05: 表->代码。edge(含该表) -> lineage_evidence.evidence_ref(source_path:line)。
    MEDIUM 4: 给 source_file 级; container(类.方法)若 05 有则附上, .sql 为空只到 file。"""
    out: list[dict] = []
    with engine_05.connect() as c:
        edges = c.execute(select(L.lineage_edges.c.edge_id).where(
            (L.lineage_edges.c.src_table == table) | (L.lineage_edges.c.dst_table == table))).fetchall()
        eids = [e.edge_id for e in edges]
        if not eids:
            return out
        evs = c.execute(select(L.lineage_evidence.c.evidence_ref).where(
            L.lineage_evidence.c.edge_id.in_(eids))).fetchall()
        for ev in evs:
            src = (ev.evidence_ref or "").split(":")[0]
            if src:
                out.append({"source_file": src, "bind_level": "source_file"})
    return out
