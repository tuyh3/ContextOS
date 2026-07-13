"""05 维证据 tool 函数(Plan 10 Task 4 / Block 1b Task 13)。

MCP/CLI 共用的原子查询薄层: 查已 build 的血缘表(lineage_edges / sql_templates)+
(router 在时)Oracle 系统视图元数据,全返回纯 dict/list,**不碰 MCP 协议**。
mcp_server/tools/* 负责 call 这些函数 + 异常转 ToolError + 脱敏(Task 7),CLI/Python lib
直接复用本层不依赖 mcp_server。

Block 1b Task 13: querier= 参数改名 router=(DbRouter|None),加 _route helper。
- router=None(离线) -> 走降级分支,返回结构完整 dict + note='oracle_offline'。
- router 在时: _route 按 owner 路由到单库 querier; 解不出 owner 时 fan-out 所有库合并。
- lookup_table 先解析 eff_owner(caller 给的优先,否则 router.resolve_owner_for_table),
  eff_owner 同时用于路由 + SQL bind params,保证"路由到正确库 + SQL 绑正确 owner"。
- lineage/dependency 对每个 querier 累加合并结果(schema 不重叠时等价单库)。
- sequence live 段取首个有结果的 querier。
- search_sql 纯本地不动。

fan-out 韧性
------------
每个 querier 的 execute_query 调用包在 per-querier try/except 里: 查询期异常
(ORA-03113 end-of-file / ORA-12541 no listener / ORA-01013 timeout 等半宕态)
被 catch + log.warning + continue,不允许单库故障逃逸整个 lookup 循环,
镜像 DbRouter._connect_cached 对 *connect* 失败的降级语义。

安全
----
- 所有 Oracle SQL 用 bind params(:owner / :tbl / :name; bind 名避开 Oracle 保留字 TABLE,
  否则真库 ORA-01745),经 execute_query 传 params。**绝不**把 owner/table/name 字符串拼进
  SQL 文本(注入面)。
- 每个函数顶部对 table/owner/name 做基本校验(非空、无分号/SQL 片段),纵深防御即便已参数化。
- router=None(离线)时查 Oracle 的函数走降级分支,返回结构完整 dict(本地血缘部分 +
  note='oracle_offline'),绝不抛。
- search_sql 纯本地(查 sql_templates),字面匹配 pattern,无 Oracle。
- fresh 环境(血缘表族未建,如只跑过 init --only code 的干净库): 查本地表前先
  store.existing_tables 判存在,缺表视同"空血缘"(计数 0 / 列表空)+ note 含
  'lineage_not_built',绝不裸抛 OperationalError(2026-07-04 runbook 冷验证复现)。
  已建 + 离线时 note 恒为精确 'oracle_offline' 不变(存量消费方按 == 匹配)。

复用: execute_query(oracle_metadata.py,只读闸门 + ROWNUM 包装 + bind params)、
store.lineage_edges / store.sql_templates schema。
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.engine import Engine

from contextos.lineage import store
from contextos.lineage.oracle_metadata import execute_query

_OFFLINE_NOTE = "oracle_offline"
_NOT_BUILT_NOTE = "lineage_not_built"
_SNIPPET_MAX = 200

# 合法 SQL identifier(table/owner/sequence/view 名): 字母/下划线开头 + 字母数字 _ $ # . ,
# 允许 owner.table 形式的点。显式排除分号 / 引号 / 空白 / 注释符,抓注入 + 配置 typo。
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#.]{0,255}$")


# --------------------------------------------------------------------------- _route helper


def _route(router: Any, *, owner: str = "", table: str = "") -> list[Any]:
    """返回该 lookup 应查询的 querier 列表: owner 可定位 -> 单库; 否则 fan-out。
    router=None(离线) -> []。
    """
    if router is None:
        return []
    if owner:
        q = router.querier_for_owner(owner)
        if q is not None:
            return [q]
    if table:
        ow = router.resolve_owner_for_table(table)
        if ow:
            q = router.querier_for_owner(ow)
            if q is not None:
                return [q]
    return router.fan_out()


def _set_note(result: dict[str, Any], *parts: str) -> None:
    """把非空 note 片段拼进 result['note'](分号连接); 全空则不落 note 键。

    单一 note(如已建 + 离线)保持裸值 'oracle_offline' 不变——存量消费方按 == 匹配;
    双态(fresh + 离线)拼 'lineage_not_built; oracle_offline'(该态修复前直接崩,
    无存量消费方,子串匹配友好)。
    """
    notes = [p for p in parts if p]
    if notes:
        result["note"] = "; ".join(notes)


def _validate_ident(value: str, *, field: str) -> str:
    """table/owner/name 校验: 非空 + 合法 identifier(无分号/引号/SQL 片段)。

    参数化已挡条件扩展,这里加 identifier 闸门做纵深防御(同 oracle_metadata._validate_owner
    的思路): 非法值(含 ; ' " 空格 SQL 片段)直接拒,不进 SQL。
    """
    if not _IDENT_RE.match(value or ""):
        raise ValueError(f"非法 {field} identifier: {value!r}")
    return value


# --------------------------------------------------------------------------- search_sql


def search_sql(engine: Engine, *, pattern: str, limit: int = 20) -> list[dict[str, Any]]:
    """grep sql_templates.sql_text 字面包含 pattern。纯本地,无 Oracle。

    返回 [{template_id, source_file, container, recovery_mode, confidence, snippet}]。
    空 pattern -> []。limit 截断结果条数(防 runaway)。
    """
    if not pattern:
        return []
    cap = max(0, int(limit))
    if cap == 0:
        return []
    tpl = store.sql_templates
    if not store.existing_tables(engine, tpl.name):
        return []  # fresh 环境表未建: 视同无模板, 不裸抛

    # 大小写不敏感包含(spec 附录 G): MySQL 场景表名在 SQL 文本里大小写混用
    # (小写 DDL vs 大写 Java 引用), icontains 让任一大小写 pattern 都能命中。
    # 对既有 Oracle 只增匹配不减(向后兼容); SQLite LIKE 本就不敏感, PG/信创 PG 靠 ilike。
    stmt = (
        select(tpl.c.template_id, tpl.c.source_file, tpl.c.container,
               tpl.c.sql_text, tpl.c.recovery_mode, tpl.c.confidence)
        .where(tpl.c.sql_text.icontains(pattern))
        .limit(cap)
    )
    out: list[dict[str, Any]] = []
    with engine.connect() as conn:
        for r in conn.execute(stmt):
            m = r._mapping
            sql_text = m["sql_text"] or ""
            out.append({
                "template_id": m["template_id"],
                "source_file": m["source_file"],
                "container": m["container"],
                "recovery_mode": m["recovery_mode"],
                "confidence": m["confidence"],
                "snippet": sql_text[:_SNIPPET_MAX],
            })
    return out


# --------------------------------------------------------------------------- lookup_table


def lookup_table(engine: Engine, *, table: str, owner: str = "",
                 router: Any = None) -> dict[str, Any]:
    """表元数据: lineage_edges 里该表的边计数 + (router 在时)Oracle 列/注释。

    返回 {table, owner, columns:[{column_name, data_type}], comment, edges_in, edges_out,
    note?}。router=None 时只出本地血缘部分(columns=[]、comment=""、note='oracle_offline')。

    eff_owner: caller 给的 owner 优先; 没给则从 routing 解析(owner 既定路由也填 SQL bind)。
    eff_owner 歧义未解时 SQL 绑空 owner -> live 列富化返空, 本地血缘仍出(诚实降级)。
    """
    _validate_ident(table, field="table")
    if owner:
        _validate_ident(owner, field="owner")

    # 表身份大小写不敏感(spec 附录 G): edges 内部恒为大写(NameResolver 两侧 upper 折叠),
    # 故边匹配用 upper 后的 table_key。对 Oracle(入参已大写)是 no-op; MySQL 小写入参补匹配。
    # 返回的 table 字段与 live Oracle 查询仍用原始入参(不改显示/Oracle live 路径)。
    table_key = (table or "").upper()
    edges = store.lineage_edges
    lineage_built = bool(store.existing_tables(engine, edges.name))
    edges_in = edges_out = 0
    if lineage_built:
        with engine.connect() as conn:
            edges_out = len(conn.execute(
                select(edges.c.edge_id).where(edges.c.src_table == table_key)
            ).all())
            edges_in = len(conn.execute(
                select(edges.c.edge_id).where(edges.c.dst_table == table_key)
            ).all())

    # eff_owner: caller 给的优先; 没给则从 routing 解析(同一个 owner 既定路由也填 SQL bind)。
    eff_owner = owner
    if not eff_owner and router is not None:
        eff_owner = router.resolve_owner_for_table(table) or ""

    result: dict[str, Any] = {
        "table": table,
        "owner": eff_owner,
        "columns": [],
        "comment": "",
        "edges_in": edges_in,
        "edges_out": edges_out,
    }

    built_note = "" if lineage_built else _NOT_BUILT_NOTE
    queriers = _route(router, owner=eff_owner, table=table)
    if not queriers:
        _set_note(result, built_note, _OFFLINE_NOTE)
        return result
    _set_note(result, built_note)

    # bind 名避开 Oracle 保留字: TABLE 是保留字, :table 在真库报 ORA-01745(用 :tbl)。
    params = {"owner": eff_owner, "tbl": table}
    for q in queriers:
        try:
            col_rows = execute_query(
                q,
                "SELECT column_name, data_type FROM ALL_TAB_COLUMNS "
                "WHERE owner = :owner AND table_name = :tbl ORDER BY column_id",
                500,
                params=params,
            )
        except Exception as exc:
            log.warning("lookup_table: querier %r query failed, skipping: %s", q, exc)
            continue
        if col_rows:
            result["columns"] = [
                {"column_name": r.get("COLUMN_NAME"), "data_type": r.get("DATA_TYPE")}
                for r in col_rows
            ]
            try:
                cmt_rows = execute_query(
                    q,
                    "SELECT comments FROM ALL_TAB_COMMENTS "
                    "WHERE owner = :owner AND table_name = :tbl",
                    5,
                    params=params,
                )
            except Exception as exc:
                log.warning("lookup_table: comment query on querier %r failed: %s", q, exc)
                cmt_rows = []
            result["comment"] = (cmt_rows[0].get("COMMENTS") or "") if cmt_rows else ""
            break  # schema 不重叠: 首个有 columns 的库即是来源库
    return result


# --------------------------------------------------------------------------- lookup_lineage


def lookup_lineage(engine: Engine, *, table: str, direction: str = "both",
                   router: Any = None) -> dict[str, Any]:
    """上下游血缘三路合并: lineage_edges(本地)+ (router 在时)ALL_DEPENDENCIES + ALL_SYNONYMS。

    返回 {table, upstream:[{table, relation_type, source}], downstream:[...], note?}。
    direction in {'up','down','both'} 控制返回侧;router=None -> 仅本地 + note。
    多库: 对每个 querier 累加(schema 不重叠, 同表名不在多库, 去重 seen_* 处理)。
    """
    _validate_ident(table, field="table")
    # 表身份大小写不敏感(spec 附录 G): edges 恒大写, 边匹配用 upper key(同 lookup_table)。
    table_key = (table or "").upper()
    want_up = direction in ("up", "both")
    want_down = direction in ("down", "both")

    edges = store.lineage_edges
    upstream: list[dict[str, Any]] = []
    downstream: list[dict[str, Any]] = []
    seen_up: set[str] = set()
    seen_down: set[str] = set()

    lineage_built = bool(store.existing_tables(engine, edges.name))
    if lineage_built:
        with engine.connect() as conn:
            if want_down:
                for r in conn.execute(
                    select(edges.c.dst_table, edges.c.relation_type)
                    .where(edges.c.src_table == table_key)
                ):
                    tbl = r._mapping["dst_table"]
                    if tbl and tbl not in seen_down:
                        seen_down.add(tbl)
                        downstream.append({"table": tbl,
                                           "relation_type": r._mapping["relation_type"],
                                           "source": "lineage_edges"})
            if want_up:
                for r in conn.execute(
                    select(edges.c.src_table, edges.c.relation_type)
                    .where(edges.c.dst_table == table_key)
                ):
                    tbl = r._mapping["src_table"]
                    if tbl and tbl not in seen_up:
                        seen_up.add(tbl)
                        upstream.append({"table": tbl,
                                         "relation_type": r._mapping["relation_type"],
                                         "source": "lineage_edges"})

    result: dict[str, Any] = {"table": table, "upstream": upstream,
                              "downstream": downstream}

    built_note = "" if lineage_built else _NOT_BUILT_NOTE
    queriers = _route(router, table=table)
    if not queriers:
        _set_note(result, built_note, _OFFLINE_NOTE)
        return result
    _set_note(result, built_note)

    # bind 名避开 Oracle 保留字 TABLE(ORA-01745): 用 :tbl。
    params = {"tbl": table}
    for q in queriers:
        # ALL_DEPENDENCIES: 谁引用了该表(上游来源)。
        try:
            dep_rows = execute_query(
                q,
                "SELECT owner, name, type, referenced_name FROM ALL_DEPENDENCIES "
                "WHERE referenced_name = :tbl",
                500,
                params=params,
            )
        except Exception as exc:
            log.warning("lookup_lineage: querier %r ALL_DEPENDENCIES failed, skipping: %s",
                        q, exc)
            continue
        for r in dep_rows:
            name = r.get("NAME")
            if want_up and name and name not in seen_up:
                seen_up.add(name)
                upstream.append({"table": name,
                                 "relation_type": (r.get("TYPE") or "DEPENDENCY"),
                                 "source": "ALL_DEPENDENCIES"})
        # ALL_SYNONYMS: 该表的同义词(别名也算关联视图)。
        try:
            syn_rows = execute_query(
                q,
                "SELECT synonym_name, table_owner, table_name FROM ALL_SYNONYMS "
                "WHERE table_name = :tbl",
                500,
                params=params,
            )
        except Exception as exc:
            log.warning("lookup_lineage: querier %r ALL_SYNONYMS failed, skipping: %s",
                        q, exc)
            syn_rows = []
        for r in syn_rows:
            syn = r.get("SYNONYM_NAME")
            if want_up and syn and syn not in seen_up:
                seen_up.add(syn)
                upstream.append({"table": syn, "relation_type": "SYNONYM",
                                 "source": "ALL_SYNONYMS"})
    return result


# --------------------------------------------------------------------------- lookup_dependency


def lookup_dependency(engine: Engine, *, name: str,
                      router: Any = None) -> dict[str, Any]:
    """view/procedure 反向依赖(谁依赖 name): ALL_DEPENDENCIES。

    返回 {name, dependents:[{owner, name, type}], note?}。
    router=None -> {name, dependents:[], note:'oracle_offline'}。
    多库: 对每个 querier 累加 dependents(去重 seen)。
    """
    _validate_ident(name, field="name")
    result: dict[str, Any] = {"name": name, "dependents": []}

    queriers = _route(router, table=name)
    if not queriers:
        result["note"] = _OFFLINE_NOTE
        return result

    seen: set[tuple[str, str]] = set()
    dependents: list[dict[str, Any]] = []
    for q in queriers:
        try:
            rows = execute_query(
                q,
                "SELECT owner, name, type FROM ALL_DEPENDENCIES "
                "WHERE referenced_name = :name",
                500,
                params={"name": name},
            )
        except Exception as exc:
            log.warning("lookup_dependency: querier %r query failed, skipping: %s", q, exc)
            continue
        for r in rows:
            key = (r.get("OWNER") or "", r.get("NAME") or "")
            if key not in seen:
                seen.add(key)
                dependents.append({
                    "owner": r.get("OWNER"),
                    "name": r.get("NAME"),
                    "type": r.get("TYPE"),
                })
    result["dependents"] = dependents
    return result


# --------------------------------------------------------------------------- lookup_sequence


def lookup_sequence(engine: Engine, *, name: str,
                    router: Any = None) -> dict[str, Any]:
    """sequence 元数据(ALL_SEQUENCES)+ lineage 里对该名的代码引用(sql_templates 字面命中)。

    返回 {name, sequence:{...}|None, code_refs:[{template_id, source_file, container}], note?}。
    router=None -> sequence=None + note。code_refs 纯本地恒查(不依赖 Oracle)。
    多库: live 段取首个有结果的 querier(sequence 在哪库返哪库的)。
    """
    _validate_ident(name, field="name")

    # 本地: sql_templates 里字面提到该 sequence 名的模板(代码引用线索)。
    tpl = store.sql_templates
    lineage_built = bool(store.existing_tables(engine, tpl.name))
    code_refs: list[dict[str, Any]] = []
    if lineage_built:
        with engine.connect() as conn:
            for r in conn.execute(
                select(tpl.c.template_id, tpl.c.source_file, tpl.c.container)
                .where(tpl.c.sql_text.contains(name))
                .limit(50)
            ):
                m = r._mapping
                code_refs.append({"template_id": m["template_id"],
                                  "source_file": m["source_file"],
                                  "container": m["container"]})

    result: dict[str, Any] = {"name": name, "sequence": None, "code_refs": code_refs}

    built_note = "" if lineage_built else _NOT_BUILT_NOTE
    queriers = _route(router, table=name)
    if not queriers:
        _set_note(result, built_note, _OFFLINE_NOTE)
        return result
    _set_note(result, built_note)

    for q in queriers:
        try:
            rows = execute_query(
                q,
                "SELECT sequence_owner, sequence_name, min_value, max_value, increment_by, "
                "last_number, cycle_flag "
                "FROM ALL_SEQUENCES WHERE sequence_name = :name",
                5,
                params={"name": name},
            )
        except Exception as exc:
            log.warning("lookup_sequence: querier %r query failed, skipping: %s", q, exc)
            continue
        if rows:
            r = rows[0]
            result["sequence"] = {
                "sequence_owner": r.get("SEQUENCE_OWNER"),
                "sequence_name": r.get("SEQUENCE_NAME"),
                "min_value": r.get("MIN_VALUE"),
                "max_value": r.get("MAX_VALUE"),
                "increment_by": r.get("INCREMENT_BY"),
                "last_number": r.get("LAST_NUMBER"),
                "capacity": _sequence_capacity(r.get("MIN_VALUE"), r.get("MAX_VALUE"),
                                               r.get("LAST_NUMBER"), r.get("CYCLE_FLAG")),
            }
            break  # 取首个有结果的库
    return result


def _sequence_capacity(min_value: Any, max_value: Any, last_number: Any,
                       cycle_flag: Any) -> dict[str, Any]:
    """usage_pct =(last-min)/(max-min)*100, >80% 告警, CYCLE=Y 提示(移植 LP)。
    用 Decimal 避免 Oracle 大数精度问题; 任一值缺/坏 -> usage_pct=None 不告警(fail-safe)。"""
    from decimal import Decimal, InvalidOperation
    cycle = str(cycle_flag or "N").upper() == "Y"
    try:
        mn, mx, last = Decimal(str(min_value)), Decimal(str(max_value)), Decimal(str(last_number))
        span = mx - mn
        if span <= 0:
            return {"usage_pct": None, "alert": False, "cycle": cycle}
        pct = float((last - mn) / span * 100)
    except (InvalidOperation, ValueError, TypeError):
        return {"usage_pct": None, "alert": False, "cycle": cycle}
    return {"usage_pct": pct, "alert": pct > 80.0, "cycle": cycle}
