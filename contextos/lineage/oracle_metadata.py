"""Oracle live 元数据层(§8 + §10): 查 Oracle 系统视图填 store 元数据表。

复用 Plan 0:
  - oracle_gate.assert_query_is_readonly: 只读 SQL 闸门(execute_query 兜底)
  - sqlcl_mcp.connect_from_profile / OracleClient.query: 真连测试库白名单
本层只负责: ROWNUM 包装 + max_rows + 元数据 SQL + 行 -> store dict。
有 live Oracle -> 富化(NameResolver 拿 owner/synonym/view/fk + provider business_relevance);
无 -> 不调本层, build/provider 走离线降级。
"""
from __future__ import annotations

import fnmatch
import logging
import re
from typing import Any, Callable, Protocol, Sequence

from contextos.db_provider.oracle_gate import assert_query_is_readonly
from contextos.lineage import store

logger = logging.getLogger(__name__)

_MAX_ROWS_HARD = 1000              # execute_query ad-hoc 查询硬上限(§8.2 bounded helper)
_META_MAX_ROWS = 1_000_000        # 元数据全量快照拉取上限(防 runaway; 远超任何真实 schema 表数)

# 合法 Oracle 非引号 identifier(schema/owner 名): 字母开头 + 字母数字 _ $ #, <=128。
_OWNER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")


def _safe_int(v: Any, default: int = 0) -> int:
    """脏值兜底成 int(对齐 Plan 08 _safe_int/_safe_float fail-safe 风格)。

    issue #2: 驱动可能返回非数字 COLUMN_ID(如 'N/A'); 直接 int() 抛 ValueError 会被
    refresh_object_metadata 的 'except ValueError: raise' 误当作 _validate_owner 的 K1
    注入错误冒泡崩整轮刷新。数据脏值 != 注入闸门, 这里吞成 default 而非冒泡。"""
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _validate_owner(owner: str) -> str:
    """owner = Oracle schema 名, 必须是合法 identifier(防注入 + 抓配置 typo)。

    review Finding #4 配套: owner 来自 profile/CLI(operator), 在 bind params 之外
    再加 identifier 闸门(纵深防御); 非法 owner(含引号/空格/SQL 片段)直接拒。"""
    if not _OWNER_RE.match(owner or ""):
        raise ValueError(f"非法 Oracle owner identifier: {owner!r}")
    return owner


def _owners_in(owners: "Sequence[str]") -> tuple[str, dict[str, Any]]:
    """方案 B 批量: 把 N 个 owner 一次塞进 `OWNER IN (:o0, :o1, ...)` —— 一条查询代替 N 条
    逐 owner 查(Oracle 慢字典视图只评估一次, 是 ~47min -> 分钟级的关键)。

    返回 (IN 子句, bind params)。owner 走 bind(:oN, 防注入)+ 逐个 _validate_owner(纵深 K1)。
    owner 列表来自 discover_owners 的运行时自动发现, **不是写死的客户 owner**(对比 LP 写死
    'AD'/'CD'/... 的耦合)。空列表抛 ValueError(调用方应先过滤空 owner 实例)。"""
    if not owners:
        raise ValueError("_owners_in: owners 不能为空")
    for o in owners:
        _validate_owner(o)
    params: dict[str, Any] = {f"o{i}": o for i, o in enumerate(owners)}
    clause = "(" + ", ".join(f":o{i}" for i in range(len(owners))) + ")"
    return clause, params


def _bulkify(per_owner_sql: str, owners: "Sequence[str]") -> tuple[str, dict[str, Any]]:
    """把含 '= :owner' 的 per-owner 查询改成 'IN (:o0, ...)' 批量版 + bind params。
    每个 _Q_* 恰好一处 '= :owner'(同义词/dblink 形如 '= :owner OR OWNER = ''PUBLIC''', 一并转)。"""
    clause, params = _owners_in(owners)
    return per_owner_sql.replace("= :owner", f"IN {clause}"), params


class _Querier(Protocol):
    # 只声明实际用到的签名(sql + 可选 params); 不加 **kw(否则要求实现接受任意 kwarg,
    # 真 OracleClient.query 只接受 keyword-only arraysize 会判不 conform, review pyright 修)。
    def query(self, sql: str,
              params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...


def execute_query(querier: _Querier, sql: str, max_rows: int = 100, *,
                  params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """只读查询(oracle_gate 闸门 + ROWNUM 包装 + max_rows + bind params)。

    querier = sqlcl_mcp.OracleClient(context manager 内)或任何有 .query 的对象。
    params 走 bind(Finding #4: 不字符串拼接), OracleClient.query 原生支持。
    """
    assert_query_is_readonly(sql)             # 写/多语句/非 SELECT -> OracleSafetyError
    clean = sql.strip().rstrip(";")
    capped = min(max_rows, _MAX_ROWS_HARD)
    wrapped = f"SELECT * FROM ({clean}) WHERE ROWNUM <= {capped + 1}"
    rows = querier.query(wrapped, params)
    return rows[:capped]


def _with_table_exclusions(sql: str, patterns: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """方案 B: 把表名排除包成子查询 `SELECT * FROM (orig) WHERE NOT REGEXP_LIKE(TABLE_NAME,:exN) ...`。

    orig 的结果必须暴露 TABLE_NAME 列 —— 本层只对有 TABLE_NAME 的 table 类查询用
    (tab_comments / columns / indexes / constraints / fks)。正则走 bind(:exN), 无注入;
    服务端排除(Oracle 侧不返这些行), 不是拉回来再 filter。空 patterns -> 原样返回(零行为变更)。
    Oracle REGEXP_LIKE 语法(用 [0-9] 非 \\d), 由 profile.tables.exclude_table_patterns 提供。

    注(FK 查询的列消解): _Q_FKS 的 SELECT 含两列同名前的 TABLE_NAME —— `ac.TABLE_NAME`(源表)与
    `r.TABLE_NAME AS FK_REF_TABLE`(被引表, 已别名)。外层不带前缀的 TABLE_NAME 按 Oracle 列消解
    解析为子查询输出里**第一个** TABLE_NAME 即 ac.TABLE_NAME(源表)—— 正是想按源表过滤, 语义正确。
    其余 4 个查询各只有一个 TABLE_NAME 列, 无歧义。"""
    if not patterns:
        return sql, {}
    conds = " AND ".join(f"NOT REGEXP_LIKE(TABLE_NAME, :ex{i})" for i in range(len(patterns)))
    params = {f"ex{i}": p for i, p in enumerate(patterns)}
    return f"SELECT * FROM ({sql}) WHERE {conds}", params


def _fetch_all_meta(querier: _Querier, sql: str,
                    params: dict[str, Any] | None = None, *,
                    exclude_patterns: Sequence[str] = ()) -> list[dict[str, Any]]:
    """元数据全量拉取(Finding #2): 不套 1000 行硬上限, 取完整快照(全量覆盖语义)。

    避免部分快照让在线 validate 丢双 unknown 边。仍走只读 gate + 极高安全上限(防 runaway)。
    exclude_patterns(方案 B): 非空时服务端按表名正则排除(只用于暴露 TABLE_NAME 的 table 类查询)。"""
    assert_query_is_readonly(sql)
    clean = sql.strip().rstrip(";")
    if exclude_patterns:
        clean, ex_params = _with_table_exclusions(clean, exclude_patterns)
        params = {**(params or {}), **ex_params}     # 新 dict, 不改调用方共享的 params
    wrapped = f"SELECT * FROM ({clean}) WHERE ROWNUM <= {_META_MAX_ROWS}"
    rows = querier.query(wrapped, params)
    if len(rows) >= _META_MAX_ROWS:        # 撞安全上限 = 疑似被截断, 别静默(reviewer Minor #1)
        logger.warning("metadata pull hit safety ceiling (%d rows); snapshot may be "
                       "truncated: %.80s", _META_MAX_ROWS, clean)
    return rows


_Q_TAB_COMMENTS = (
    "SELECT c.OWNER, c.TABLE_NAME, c.TABLE_TYPE, c.COMMENTS "
    "FROM ALL_TAB_COMMENTS c WHERE c.OWNER = :owner"
)
_Q_SYNONYMS = (
    "SELECT SYNONYM_NAME, TABLE_OWNER, TABLE_NAME, DB_LINK "
    "FROM ALL_SYNONYMS WHERE OWNER = :owner OR OWNER = 'PUBLIC'"
)
_Q_FKS = (
    "SELECT ac.TABLE_NAME, r.TABLE_NAME AS FK_REF_TABLE "
    "FROM ALL_CONSTRAINTS ac JOIN ALL_CONSTRAINTS r ON ac.R_CONSTRAINT_NAME = r.CONSTRAINT_NAME "
    "WHERE ac.CONSTRAINT_TYPE = 'R' AND ac.OWNER = :owner"
)

# --- Block 1a+1b: 7 类对象元数据 + 对象依赖 + dblinks 查询(§10)。owner 走 :owner bind(K1 防注入);
#     LISTAGG 用 ON OVERFLOW TRUNCATE 避开 K8 4000 字截断。---
_Q_COLUMNS = (
    "SELECT c.OWNER, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.NULLABLE, c.COLUMN_ID, "
    "cc.COMMENTS "
    "FROM ALL_TAB_COLUMNS c LEFT JOIN ALL_COL_COMMENTS cc "
    "ON c.OWNER = cc.OWNER AND c.TABLE_NAME = cc.TABLE_NAME AND c.COLUMN_NAME = cc.COLUMN_NAME "
    "WHERE c.OWNER = :owner"
)
_Q_INDEXES = (
    "SELECT i.OWNER, i.INDEX_NAME, i.TABLE_NAME, i.UNIQUENESS, "
    "LISTAGG(ic.COLUMN_NAME, ',') WITHIN GROUP (ORDER BY ic.COLUMN_POSITION) "
    "ON OVERFLOW TRUNCATE AS COLUMN_LIST "
    "FROM ALL_INDEXES i JOIN ALL_IND_COLUMNS ic "
    "ON i.OWNER = ic.INDEX_OWNER AND i.INDEX_NAME = ic.INDEX_NAME "
    "WHERE i.OWNER = :owner "
    "GROUP BY i.OWNER, i.INDEX_NAME, i.TABLE_NAME, i.UNIQUENESS"
)
_Q_CONSTRAINTS = (
    "SELECT OWNER, CONSTRAINT_NAME, TABLE_NAME, CONSTRAINT_TYPE, R_OWNER, "
    "R_CONSTRAINT_NAME, SEARCH_CONDITION "
    "FROM ALL_CONSTRAINTS WHERE OWNER = :owner"
)
_Q_SEQUENCES = (
    "SELECT SEQUENCE_OWNER, SEQUENCE_NAME, MIN_VALUE, MAX_VALUE, INCREMENT_BY, "
    "LAST_NUMBER, CACHE_SIZE, CYCLE_FLAG "
    "FROM ALL_SEQUENCES WHERE SEQUENCE_OWNER = :owner"
)
_Q_VIEWS = "SELECT OWNER, VIEW_NAME FROM ALL_VIEWS WHERE OWNER = :owner"  # 不取 TEXT(K2)
_Q_PROCEDURES = (
    "SELECT OWNER, OBJECT_NAME, OBJECT_TYPE FROM ALL_OBJECTS "
    "WHERE OWNER = :owner AND OBJECT_TYPE IN ('PROCEDURE','FUNCTION','PACKAGE')"
)
_Q_DEPENDENCIES = (
    "SELECT OWNER, NAME, TYPE, REFERENCED_OWNER, REFERENCED_NAME, REFERENCED_TYPE, "
    "REFERENCED_LINK_NAME FROM ALL_DEPENDENCIES WHERE OWNER = :owner"
)
_Q_DBLINKS = (
    "SELECT OWNER, DB_LINK, HOST, USERNAME, CREATED FROM ALL_DB_LINKS "
    "WHERE OWNER = :owner OR OWNER = 'PUBLIC'"
)


def _dedupe_by_pk(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 (owner, template_name) 复合 PK 去重, 保留首个, 避免 intra-batch PK 冲突。

    裁决 5 身份锚 = owner.table: 同名表跨 owner 是不同行, 不去重(Finding #1 修)。"""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        k = (r.get("owner", ""), r["template_name"])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def _fetch_metadata_bulk(querier: _Querier, owners: Sequence[str], *, db_name: str = "",
                         exclude_table_patterns: Sequence[str] = ()) -> dict[str, list]:
    """方案 B 批量: 一次拉**多** owner 的表级元数据(每类一条 `OWNER IN (...)` 查, 非逐 owner)。

    tab_comments 必查(失败抛, 供 refresh 保留旧快照); synonym/fk 用 _safe_query 降级返空。
    结果按各行的 OWNER 字段归属(查询都 SELECT 了 OWNER), 不再依赖单 owner 入参。
    exclude_table_patterns(方案 B 表名排除): 只给有 TABLE_NAME 的 tab_comments / fks 传。"""
    tab_sql, p_tab = _bulkify(_Q_TAB_COMMENTS, owners)
    tab_rows = _fetch_all_meta(querier, tab_sql, p_tab,           # 全量必查, 失败抛
                               exclude_patterns=exclude_table_patterns)
    syn_sql, p_syn = _bulkify(_Q_SYNONYMS, owners)
    syn_rows = _safe_query(querier, syn_sql, p_syn)
    fk_sql, p_fk = _bulkify(_Q_FKS, owners)
    fk_rows = _safe_query(querier, fk_sql, p_fk, exclude_patterns=exclude_table_patterns)
    md = [dict(template_name=(r.get("TABLE_NAME") or "").upper(), db_name=db_name,
               owner=r.get("OWNER") or "", comment=r.get("COMMENTS") or "",
               dataset_type=(r.get("TABLE_TYPE") or "TABLE").upper())
          for r in tab_rows if r.get("TABLE_NAME")]
    syn = [dict(synonym_name=(r.get("SYNONYM_NAME") or "").upper(), db_name=db_name,
                table_owner=r.get("TABLE_OWNER") or "", table_name=(r.get("TABLE_NAME") or "").upper(),
                db_link=r.get("DB_LINK") or "")
           for r in syn_rows if r.get("SYNONYM_NAME")]
    fks = [dict(table_a=(r.get("TABLE_NAME") or "").upper(),
                table_b=(r.get("FK_REF_TABLE") or "").upper())
           for r in fk_rows if r.get("TABLE_NAME") and r.get("FK_REF_TABLE")]
    return {"tables": _dedupe_by_pk(md), "synonyms": syn, "fks": fks}


def fetch_metadata(querier: _Querier, *, owner: str, db_name: str = "",
                   exclude_table_patterns: Sequence[str] = ()) -> dict[str, list]:
    """单 owner 便捷包装 -> 批量(一元素 owner 列表)。主逻辑见 _fetch_metadata_bulk。"""
    return _fetch_metadata_bulk(querier, [owner], db_name=db_name,
                                exclude_table_patterns=exclude_table_patterns)


def load_metadata_into_store(querier: _Querier, engine, *, owner: str,
                             db_name: str = "") -> dict[str, int]:
    """单 owner 拉取并写库(append; 重复调用前需 clear_metadata, 见 refresh_metadata)。"""
    data = fetch_metadata(querier, owner=owner, db_name=db_name)
    store.write_table_metadata(engine, data["tables"])
    store.write_table_synonyms(engine, data["synonyms"])
    store.write_table_fks(engine, data["fks"])
    return {"tables": len(data["tables"]), "synonyms": len(data["synonyms"]),
            "fks": len(data["fks"])}


def refresh_metadata(querier: _Querier, engine, *, owners: list[str], db_name: str = "",
                     now: str, exclude_table_patterns: Sequence[str] = ()) -> dict[str, Any]:
    """全量快照覆盖刷新。先全拉(成功)再 clear+写+盖时间戳; 任一 owner 必查失败 ->
    保留旧快照, 不清空, 不更新时间戳(refreshed=False)。`now` = ISO8601(调用方注入, 可测)。

    空 owners(配置错误)-> 不动旧快照、不盖时间戳(否则会清空好数据 + 误标 fresh,
    破坏"拉失败绝不清空"的核心承诺; 2026-06-02 审计加固)。"""
    if not owners:
        return {"refreshed": False, "reason": "no_owners",
                "tables": 0, "synonyms": 0, "fks": 0}
    try:
        data = _fetch_metadata_bulk(querier, owners, db_name=db_name,   # 方案 B: 一条批量, 非逐 owner
                                    exclude_table_patterns=exclude_table_patterns)
    except Exception as exc:  # noqa: BLE001  Oracle 断连/超时 -> 保留旧快照
        return {"refreshed": False, "reason": f"{type(exc).__name__}: {exc}",
                "tables": 0, "synonyms": 0, "fks": 0}
    md = _dedupe_by_pk(data["tables"])
    syn = data["synonyms"]
    fks = data["fks"]
    # HIGH-2: 原子全量覆盖(clear+write+set_meta 单事务)。owner_tns=None: 单库路径不管 owner_routing。
    store.replace_metadata(engine, tables=md, synonyms=syn, fks=fks,
                           owner_tns=None, refreshed_at=now)
    return {"refreshed": True, "tables": len(md), "synonyms": len(syn), "fks": len(fks)}


def is_metadata_stale(engine, ttl_hours: int, now: str) -> bool:
    """无刷新记录 / 超 TTL / 时间串坏 / 时区不匹配 -> stale。`now` = ISO8601。

    TypeError = tz-aware 与 naive 时间相减(如 smoke 用 UTC-aware、测试用 naive);
    与 ValueError(串坏)一样 fail-safe 当 stale(重拉), 不让 staleness 检查崩溃
    (2026-06-02 审计加固)。"""
    from datetime import datetime
    last = store.get_meta(engine, "metadata_refreshed_at")
    if not last:
        return True
    try:
        delta = (datetime.fromisoformat(now) - datetime.fromisoformat(last)).total_seconds()
    except (ValueError, TypeError):
        return True
    return delta > ttl_hours * 3600


def refresh_metadata_if_stale(querier: _Querier, engine, *, owners: list[str],
                              db_name: str = "", ttl_hours: int, now: str) -> dict[str, Any]:
    """TTL 闸门: 未过期跳过(不查 Oracle); 过期才全量刷新。这是 build/provider 启动时的接入点。"""
    if not is_metadata_stale(engine, ttl_hours, now):
        return {"refreshed": False, "reason": "fresh", "tables": 0, "synonyms": 0, "fks": 0}
    return refresh_metadata(querier, engine, owners=owners, db_name=db_name, now=now)


def _fetch_object_metadata_bulk(querier: _Querier, owners: Sequence[str], *,
                                db_name: str = "",
                                exclude_table_patterns: Sequence[str] = (),
                                scope: str = "full") -> dict[str, list]:
    """方案 B 批量: 一次拉**多** owner 的对象元数据(每类一条 OWNER IN 查)。
    返回 8 键: columns/indexes/constraints/sequences/views/procedures/dependencies/dblinks。

    scope(option A):
      "full"    -> 全 8 类。columns 当必查门(_fetch_all_meta 失败抛)。给将来 config 维度按
                   LP 模板归并抓列用(opt-in profile.tables.fetch_full_object_metadata)。
      "lineage" -> 只抓 dependencies(对象血缘)+ dblinks(@dblink 解析), 其余 6 类返空。
                   这是数据库维度默认: 表级血缘不需要 columns/indexes/constraints(per-table 重查,
                   某大型客户满库抓列 ~40min 墙, 唯一消费方是 config), sequences/views/procedures 全仓
                   暂无消费方。canary 必查门改成 dependencies(同样 _fetch_all_meta 失败抛)。

    必查门机制: 真 Oracle 整库失联(ORA-12541/12537 等)时, 必查门查询抛, 让 refresh_object_metadata
    走 except 保留旧快照, 而非全 _safe_query 吞成 [] 误标 refreshed=True + clear WIPE 好快照(blocker)。
    其余类用 _safe_query 缺权限降级返空。结果按各行 OWNER/SEQUENCE_OWNER 归属, 不依赖单 owner 入参。"""
    clause, params = _owners_in(owners)

    def _b(q: str) -> str:
        return q.replace("= :owner", f"IN {clause}")

    if scope == "lineage":
        # 表级血缘只需 dependencies + dblinks。lineage scope 只抓这两类, 两者都当必查门
        # (_fetch_all_meta 失败抛 -> refresh 走 except 保留旧快照): 否则 dblinks 走 _safe_query 会吞掉
        # "dependencies canary 成功后、dblinks 抓取时断连" 的异常 -> 写空 dblinks WIPE 旧快照而谎报
        # 成功(review blocker)。两个都是默认 PUBLIC 可读的标准视图(ALL_DEPENDENCIES / ALL_DB_LINKS),
        # 不会因缺权限误抛; 合法为空(无依赖/无 dblink)时 _fetch_all_meta 正常返 [] 不抛。
        dep_rows = _fetch_all_meta(querier, _b(_Q_DEPENDENCIES), params)   # 必查门
        dbl_rows = _fetch_all_meta(querier, _b(_Q_DBLINKS), params)        # 必查门(闭合 WIPE 窗口)
        col_rows, idx_rows, con_rows = [], [], []
        seq_rows, view_rows, proc_rows = [], [], []
    else:
        # 方案 B: 有 TABLE_NAME 的 table 类(columns/indexes/constraints)服务端按表名正则排除;
        # sequences/views/procedures/dependencies/dblinks 非表名键 + 量小, 不排。
        col_rows = _fetch_all_meta(querier, _b(_Q_COLUMNS), params,   # 必查门, 失败抛(供 refresh 保留旧快照)
                                  exclude_patterns=exclude_table_patterns)
        idx_rows = _safe_query(querier, _b(_Q_INDEXES), params, exclude_patterns=exclude_table_patterns)
        con_rows = _safe_query(querier, _b(_Q_CONSTRAINTS), params, exclude_patterns=exclude_table_patterns)
        seq_rows = _safe_query(querier, _b(_Q_SEQUENCES), params)
        view_rows = _safe_query(querier, _b(_Q_VIEWS), params)
        proc_rows = _safe_query(querier, _b(_Q_PROCEDURES), params)
        dep_rows = _safe_query(querier, _b(_Q_DEPENDENCIES), params)
        dbl_rows = _safe_query(querier, _b(_Q_DBLINKS), params)        # ALL_DB_LINKS: 缺权限降级返空
    return {
        "columns": [dict(owner=r.get("OWNER") or "",
                         table_name=(r.get("TABLE_NAME") or "").upper(),
                         column_name=(r.get("COLUMN_NAME") or "").upper(),
                         data_type=r.get("DATA_TYPE") or "", nullable=r.get("NULLABLE") or "Y",
                         comment=r.get("COMMENTS") or "", column_id=_safe_int(r.get("COLUMN_ID")),
                         db_name=db_name)
                    for r in col_rows if r.get("TABLE_NAME") and r.get("COLUMN_NAME")],
        "indexes": [dict(owner=r.get("OWNER") or "",
                         index_name=(r.get("INDEX_NAME") or "").upper(),
                         table_name=(r.get("TABLE_NAME") or "").upper(),
                         uniqueness=r.get("UNIQUENESS") or "",
                         column_list=r.get("COLUMN_LIST") or "", db_name=db_name)
                    for r in idx_rows if r.get("INDEX_NAME")],
        "constraints": [dict(owner=r.get("OWNER") or "",
                             constraint_name=(r.get("CONSTRAINT_NAME") or "").upper(),
                             table_name=(r.get("TABLE_NAME") or "").upper(),
                             constraint_type=r.get("CONSTRAINT_TYPE") or "",
                             r_owner=r.get("R_OWNER") or "",
                             r_constraint_name=r.get("R_CONSTRAINT_NAME") or "",
                             search_condition=r.get("SEARCH_CONDITION") or "", db_name=db_name)
                        for r in con_rows if r.get("CONSTRAINT_NAME")],
        "sequences": [dict(owner=r.get("SEQUENCE_OWNER") or "",
                           sequence_name=(r.get("SEQUENCE_NAME") or "").upper(),
                           min_value=str(r.get("MIN_VALUE") if r.get("MIN_VALUE") is not None else ""),
                           max_value=str(r.get("MAX_VALUE") if r.get("MAX_VALUE") is not None else ""),
                           increment_by=str(r.get("INCREMENT_BY") if r.get("INCREMENT_BY") is not None else ""),
                           last_number=str(r.get("LAST_NUMBER") if r.get("LAST_NUMBER") is not None else ""),
                           cache_size=str(r.get("CACHE_SIZE") if r.get("CACHE_SIZE") is not None else ""),
                           cycle_flag=r.get("CYCLE_FLAG") or "N", db_name=db_name)
                      for r in seq_rows if r.get("SEQUENCE_NAME")],
        "views": [dict(owner=r.get("OWNER") or "", view_name=(r.get("VIEW_NAME") or "").upper(),
                       comment="", db_name=db_name)
                  for r in view_rows if r.get("VIEW_NAME")],
        "procedures": [dict(owner=r.get("OWNER") or "",
                            object_name=(r.get("OBJECT_NAME") or "").upper(),
                            object_type=r.get("OBJECT_TYPE") or "", db_name=db_name)
                       for r in proc_rows if r.get("OBJECT_NAME")],
        "dependencies": [dict(owner=r.get("OWNER") or "", name=(r.get("NAME") or "").upper(),
                             type=r.get("TYPE") or "", referenced_owner=r.get("REFERENCED_OWNER") or "",
                             referenced_name=(r.get("REFERENCED_NAME") or "").upper(),
                             referenced_type=r.get("REFERENCED_TYPE") or "",
                             referenced_link_name=r.get("REFERENCED_LINK_NAME") or "", db_name=db_name)
                        for r in dep_rows if r.get("NAME") and r.get("REFERENCED_NAME")],
        "dblinks": [dict(owner=r.get("OWNER") or "",
                         db_link=(r.get("DB_LINK") or "").upper(),
                         host=r.get("HOST") or "", username=r.get("USERNAME") or "",
                         created=str(r.get("CREATED") or ""), db_name=db_name)
                    for r in dbl_rows if r.get("DB_LINK")],
    }


def fetch_object_metadata(querier: _Querier, *, owner: str, db_name: str = "",
                          exclude_table_patterns: Sequence[str] = ()) -> dict[str, list]:
    """单 owner 便捷包装 -> 批量(一元素 owner 列表)。主逻辑见 _fetch_object_metadata_bulk。"""
    return _fetch_object_metadata_bulk(querier, [owner], db_name=db_name,
                                       exclude_table_patterns=exclude_table_patterns)


def load_object_metadata_into_store(querier: _Querier, engine, *, owner: str,
                                    db_name: str = "") -> dict[str, int]:
    """单 owner 拉取并写库(append; 全量覆盖由 refresh_object_metadata 的 clear 管)。"""
    data = fetch_object_metadata(querier, owner=owner, db_name=db_name)
    store.write_columns(engine, data["columns"])
    store.write_indexes(engine, data["indexes"])
    store.write_constraints(engine, data["constraints"])
    store.write_sequences(engine, data["sequences"])
    store.write_views(engine, data["views"])
    store.write_procedures(engine, data["procedures"])
    store.write_dependencies(engine, data["dependencies"])
    store.write_dblinks(engine, data["dblinks"])
    return {k: len(v) for k, v in data.items()}


def refresh_object_metadata(querier: _Querier, engine, *, owners: list[str], db_name: str = "",
                            now: str, exclude_table_patterns: Sequence[str] = (),
                            scope: str = "full") -> dict[str, Any]:
    """全量快照覆盖刷新对象元数据表。先全拉(成功)再 clear+写; 任一 owner 拉失败 ->
    保留旧快照不清空(对齐 refresh_metadata 的'拉失败绝不清空'承诺)。空 owners 同样不动旧快照。

    scope 见 _fetch_object_metadata_bulk: "full"(8 类, columns 当必查门)/ "lineage"(默认数据库维度,
    只 dependencies+dblinks, dependencies 当必查门)。

    fail-safe 靠把必查门走 _fetch_all_meta(失败抛): 真 Oracle 失联时必查门查询抛 -> 落 except 分支
    保留旧快照, 不会被全 _safe_query 吞成空快照而误标 refreshed=True + clear WIPE。`except ValueError`
    只兜 _validate_owner 的 K1 注入闸门(数据脏值已由 _safe_int 兜底, 不再从这里冒泡)。"""
    if not owners:
        return {"refreshed": False, "reason": "no_owners"}
    try:
        merged = _fetch_object_metadata_bulk(querier, owners, db_name=db_name,   # 方案 B: 一条批量
                                             exclude_table_patterns=exclude_table_patterns, scope=scope)
    except ValueError:
        raise                                  # 非法 owner identifier(K1)直接抛, 不当连接失败吞
    except Exception as exc:  # noqa: BLE001    Oracle 断连/超时 -> 保留旧快照
        return {"refreshed": False, "reason": f"{type(exc).__name__}: {exc}"}
    # dblinks PK = (owner, db_link)。_Q_DBLINKS 条件含 OR OWNER = 'PUBLIC', 多 owner 循环时
    # 每次查询都返回同一批 PUBLIC dblinks -> merged 含重复行 -> write_dblinks executemany
    # 触发 UNIQUE constraint failed IntegrityError 且此时 clear 已执行(旧快照已清)。
    # 按 PK 去重保留首个, 不影响各 owner 私有 dblinks(不同 owner 同名 link 是不同 PK 行)。
    seen_dbl: set[tuple[str, str]] = set()
    deduped_dbl: list[dict] = []
    for row in merged["dblinks"]:
        pk = (row.get("owner", ""), row.get("db_link", ""))
        if pk not in seen_dbl:
            seen_dbl.add(pk)
            deduped_dbl.append(row)
    merged["dblinks"] = deduped_dbl
    # HIGH-2: 原子全量覆盖(clear 8 表 + 8 write + set_meta 单事务, 任一抛则整体回滚保旧快照)。
    store.replace_object_metadata(
        engine, columns=merged["columns"], indexes=merged["indexes"],
        constraints=merged["constraints"], sequences=merged["sequences"],
        views=merged["views"], procedures=merged["procedures"],
        dependencies=merged["dependencies"], dblinks=merged["dblinks"],
        refreshed_at=now)
    return {"refreshed": True, **{k: len(v) for k, v in merged.items()}}


def _safe_query(querier: _Querier, sql: str,
                params: dict[str, Any] | None = None, *,
                exclude_patterns: Sequence[str] = ()) -> list[dict[str, Any]]:
    """synonym/fk 查询失败(权限/视图缺)不阻塞: 降级返空。全量拉取(Finding #2)。
    exclude_patterns(方案 B): 透传给 _fetch_all_meta(只对 table 类查询传)。"""
    try:
        return _fetch_all_meta(querier, sql, params, exclude_patterns=exclude_patterns)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Task 11: 多库元数据 orchestrator + owner_routing 填充 + schema 重叠告警
# ---------------------------------------------------------------------------


class InstanceSpecP(Protocol):
    """多库刷新时每个实例的最小描述契约。"""
    tns: str
    db_name: str
    owners: list[str]


def refresh_metadata_multi(engine, instances: Sequence[InstanceSpecP], *,
                           querier_factory: Callable[[str], _Querier],
                           now: str, exclude_table_patterns: Sequence[str] = ()) -> dict[str, Any]:
    """多库表级元数据全量刷新。

    clear 一次 + 各实例各自连接 append(db_name 标来源) + 建 owner->TNS 路由 + schema 重叠告警。
    任一实例必查失败 -> 全保留旧快照不清空(对齐单库 refresh_metadata 的'拉失败绝不清空'承诺)。

    querier_factory(tns) -> 有 .query 的连接对象(调用方负责连接生命周期)。
    空 instances 早退(不动旧快照)。
    """
    if not instances:
        return {"refreshed": False, "reason": "no_instances"}
    if sum(len(s.owners) for s in instances) == 0:
        return {"refreshed": False, "reason": "no_owners",
                "tables": 0, "synonyms": 0, "fks": 0}
    all_md: list = []
    all_syn: list = []
    all_fks: list = []
    owner_tns: dict[str, str] = {}
    overlap: list[str] = []
    try:
        for spec in instances:
            q = querier_factory(spec.tns)
            for owner in spec.owners:
                owner_upper = owner.upper()
                if owner_upper in owner_tns and owner_tns[owner_upper] != spec.tns:
                    overlap.append(owner_upper)     # 跨库同 owner = 身份冲突
                owner_tns[owner_upper] = spec.tns
            if not spec.owners:                        # 空 owner 实例跳过(否则 _owners_in 抛)
                continue
            # 方案 B: 整个实例一条 OWNER IN (...) 批量查(替代逐 owner N 次), 慢字典视图只评估一次。
            logger.info("  表元数据: 实例 %s 批量抓 %d owner ...", spec.db_name, len(spec.owners))
            d = _fetch_metadata_bulk(q, spec.owners, db_name=spec.db_name,
                                     exclude_table_patterns=exclude_table_patterns)
            all_md += d["tables"]
            all_syn += d["synonyms"]
            all_fks += d["fks"]
            logger.info("  表元数据: 实例 %s 完成 -> %d 表(累计 %d)",
                        spec.db_name, len(d["tables"]), len(all_md))
    except ValueError:
        raise                                          # 非法 owner identifier(K1)直接抛
    except Exception as exc:  # noqa: BLE001          任一实例断连 -> 保留旧快照
        return {"refreshed": False, "reason": f"{type(exc).__name__}: {exc}"}
    if overlap:
        logger.warning(
            "multi-db schema overlap: owner(s) %s loaded from >1 instance; "
            "owner.table identity assumes non-overlap (red line / 裁决 5)",
            sorted(set(overlap)),
        )
    all_md = _dedupe_by_pk(all_md)
    # HIGH-2: 原子全量覆盖(clear 3 表 + write + owner_routing 覆盖 + set_meta 单事务)。
    store.replace_metadata(engine, tables=all_md, synonyms=all_syn, fks=all_fks,
                           owner_tns=owner_tns, refreshed_at=now)
    return {
        "refreshed": True,
        "tables": len(all_md),
        "synonyms": len(all_syn),
        "fks": len(all_fks),
        "instances": len(instances),
        "overlapping_owners": sorted(set(overlap)),
    }


def refresh_object_metadata_multi(engine, instances: Sequence[InstanceSpecP], *,
                                  querier_factory: Callable[[str], _Querier],
                                  now: str, exclude_table_patterns: Sequence[str] = (),
                                  scope: str = "full") -> dict[str, Any]:
    """多库对象元数据全量刷新。scope 见 _fetch_object_metadata_bulk("full" 全 8 类 / "lineage"
    只 dependencies+dblinks, 默认数据库维度走 lineage)。

    语义同 refresh_metadata_multi: clear 一次 + 各实例 append; 任一实例必查门失败 -> 保留旧快照。
    """
    if not instances:
        return {"refreshed": False, "reason": "no_instances"}
    if sum(len(s.owners) for s in instances) == 0:
        return {"refreshed": False, "reason": "no_owners"}
    keys = ("columns", "indexes", "constraints", "sequences",
            "views", "procedures", "dependencies", "dblinks")
    merged: dict[str, list] = {k: [] for k in keys}
    try:
        for spec in instances:
            q = querier_factory(spec.tns)
            if not spec.owners:                        # 空 owner 实例跳过(否则 _owners_in 抛)
                continue
            # 方案 B: 整个实例一条 OWNER IN (...) 批量查, 慢字典视图只评估一次。
            logger.info("  对象元数据: 实例 %s 批量抓 %d owner (scope=%s)...",
                        spec.db_name, len(spec.owners), scope)
            d = _fetch_object_metadata_bulk(q, spec.owners, db_name=spec.db_name,
                                            exclude_table_patterns=exclude_table_patterns, scope=scope)
            for k in keys:
                merged[k] += d[k]
            logger.info("  对象元数据: 实例 %s 完成 -> %d 依赖 / %d dblink / %d 列(累计依赖 %d)",
                        spec.db_name, len(d["dependencies"]), len(d["dblinks"]),
                        len(d["columns"]), len(merged["dependencies"]))
    except ValueError:
        raise                                          # 非法 owner identifier(K1)直接抛
    except Exception as exc:  # noqa: BLE001          任一实例断连 -> 保留旧快照
        return {"refreshed": False, "reason": f"{type(exc).__name__}: {exc}"}
    # 多实例 / 多 owner 循环时 merged 可能含重复 PK 行, 写库前按各表复合 PK 去重保留首个。
    # 场景一: dblinks: OR OWNER='PUBLIC' 使每 owner 查询都返回同一批 PUBLIC dblinks。
    # 场景二: schema overlap(同 owner 跨实例): columns/indexes/constraints/sequences/
    #         views/procedures 等均含重复 (owner, ...) 行。
    # dependencies 是 autoincrement id PK, 无冲突, 不去重。
    # 对齐单库 refresh_object_metadata line 363-370 的 dblinks 去重逻辑, 扩展至全部对象元数据。
    _pks: dict[str, list[str]] = {
        "columns":     ["owner", "table_name", "column_name"],
        "indexes":     ["owner", "index_name"],
        "constraints": ["owner", "constraint_name"],
        "sequences":   ["owner", "sequence_name"],
        "views":       ["owner", "view_name"],
        "procedures":  ["owner", "object_name"],
        "dblinks":     ["owner", "db_link"],
    }
    for key, pk_cols in _pks.items():
        seen: set[tuple[str, ...]] = set()
        deduped: list[dict] = []
        for row in merged[key]:
            pk = tuple(row.get(c, "") for c in pk_cols)
            if pk not in seen:
                seen.add(pk)
                deduped.append(row)
        merged[key] = deduped
    # HIGH-2: 原子全量覆盖(clear 8 表 + 8 write + set_meta 单事务, 任一抛则整体回滚保旧快照)。
    store.replace_object_metadata(
        engine, columns=merged["columns"], indexes=merged["indexes"],
        constraints=merged["constraints"], sequences=merged["sequences"],
        views=merged["views"], procedures=merged["procedures"],
        dependencies=merged["dependencies"], dblinks=merged["dblinks"],
        refreshed_at=now)
    return {
        "refreshed": True,
        "instances": len(instances),
        **{k: len(v) for k, v in merged.items()},
    }


# ---------------------------------------------------------------------------
# Block 2 Task 3: automatic schema discovery
# ---------------------------------------------------------------------------

_Q_OWNERS = "SELECT DISTINCT OWNER FROM ALL_OBJECTS"


def discover_owners(querier: _Querier, exclude_schemas: list[str]) -> list[str]:
    """自动发现拥有任何对象的 schema, 减去 exclude_schemas(fnmatch glob)。

    查询失败(权限/连接)-> **raise**(必查门, 走 _fetch_all_meta; 由 build_database_dimension
    捕获 -> 保留旧快照不 WIPE)。真空结果(罕见)-> []。
    **绝不用 [] 表示失败**(否则空 owners 喂 refresh_*_multi 会清表, spec §4.3 HIGH1)。
    exclude 用 fnmatch: 'SYS' 精确(无 glob 字符)、'*_STAGE' 通配; 全大写比较。"""
    rows = _fetch_all_meta(querier, _Q_OWNERS)          # 失败抛(不 catch)
    patterns = [p.upper() for p in exclude_schemas]
    out: list[str] = []
    for r in rows:
        owner = (r.get("OWNER") or "").upper()
        if not owner:
            continue
        if any(fnmatch.fnmatch(owner, p) for p in patterns):
            continue
        out.append(owner)
    return sorted(set(out))
