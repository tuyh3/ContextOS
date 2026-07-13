"""D10 trace_method_dataflow 三路 fallback(§7, production-ready 硬门槛)。

输入 method 所在文件(source_path, 来自 04 候选的 signals.file / container)。
路径 A java_table_refs 直查(v1 预期空, 裁决 3)-> B lineage_evidence 反查(主)
-> C sql_templates 反查(兜底)。合并去重, 标 evidence source。
"""
from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from sqlalchemy import select
from sqlalchemy.engine import Engine

from contextos.lineage import store
from contextos.lineage.sql_parse import _SQL_KEYWORDS, parse_sql, preprocess_sql

_SRC_A = "code-method-table-direct"
_SRC_B = "code-lineage-evidence-fallback"
_SRC_C = "code-sql-template-fallback"


def trace_method_dataflow(engine: Engine, *, source_path: str) -> list[dict[str, Any]]:
    """方法所在文件 -> 表(按表去重)。返回 [{table, relation_type, source, evidence_ref}, ...]。

    v1 = 文件级(review Finding #3 修): 按 source_path 反查, 返回该文件所有 SQL 触及的表,
    不区分文件内哪个方法。方法级(行范围过滤)需 04 给方法 line range + evidence 按语句行落区间,
    属 04b/后续 —— evidence_ref 现存 SQL 语句行(非方法声明行), 单一 method_line 前缀匹配做不到
    且数字前缀有歧义(LIKE ':20%' 会命中 :20/:200/:2000)。原 method_line 死参数已删, 不再误导下游。
    """
    seen: set[str] = set()
    hits: list[dict[str, Any]] = []
    # 查询期方言读回单一取值点(spec 4.5): build 期存的 sql_dialect, 缺失默认 oracle。
    # fresh 库(只跑过 init --only code)metadata_meta 表未建 -> 守卫后默认 oracle,
    # 不裸抛 OperationalError(fresh-env 家族纪律, 同下方各路径的 existing_tables 守卫)。
    dialect = "oracle"
    if store.existing_tables(engine, "metadata_meta"):
        dialect = store.get_meta(engine, "sql_dialect") or "oracle"

    # fresh 环境(血缘表族未建, 如只跑过 init --only code): 缺哪张表就跳过对应路径,
    # 视同空血缘返回, 不裸抛 OperationalError(同 lineage/tools.py 各 lookup)。
    present = store.existing_tables(engine, store.lineage_evidence.name,
                                    store.lineage_edges.name, store.sql_templates.name)

    # 路径 A: java_table_refs 直查 -> v1 预期空(表不存在/无数据), 跳过。
    # (裁决 3: java_* 投影属 04b, 未建; 这里恒空, 直接降级 B。)

    # 路径 B: lineage_evidence 反查(文件级: evidence_ref 形如 source_path:line)
    # LIKE 通配符转义(reviewer Minor #2): source_path 里的 _ / % / \ 当字面 + :% 精确后缀
    # (evidence_ref 恒为 path:line), 避免 My_Dao.java 误命中 MyXDao.java。
    esc = source_path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if {store.lineage_evidence.name, store.lineage_edges.name} <= present:
        with engine.connect() as conn:
            ev_rows = conn.execute(
                select(store.lineage_evidence.c.edge_id, store.lineage_evidence.c.evidence_ref)
                .where(store.lineage_evidence.c.evidence_ref.like(f"{esc}:%", escape="\\"))
            ).all()
            edge_ids = {r.edge_id for r in ev_rows}
            if edge_ids:
                edge_rows = conn.execute(
                    select(store.lineage_edges).where(store.lineage_edges.c.edge_id.in_(edge_ids))
                ).all()
                for er in edge_rows:
                    m = er._mapping
                    for tbl in (m["src_table"], m["dst_table"]):
                        if tbl and tbl not in seen:
                            seen.add(tbl)
                            hits.append(dict(table=tbl, relation_type=m["relation_type"],
                                             source=_SRC_B,
                                             evidence_ref=f"{source_path} (edge {m['edge_id']})"))

    # 路径 C: sql_templates 反查(B 未覆盖的表)
    tpl_rows = []
    if store.sql_templates.name in present:
        with engine.connect() as conn:
            tpl_rows = conn.execute(
                select(store.sql_templates).where(store.sql_templates.c.source_file == source_path)
            ).all()
    for tr in tpl_rows:
        sql_text = tr._mapping["sql_text"]
        template_id = tr._mapping["template_id"]
        # 用 parse_sql 拿到带 relation_type 的关系(join/where/subquery/write);
        # 但单表只读 SELECT 不产 relation -> 再用 sqlglot 直接抽表兜底(裁决: §7 路径 C 抽表)。
        relations, _seq, err = parse_sql(sql_text, dialect=dialect)
        rel_type_by_table: dict[str, str] = {}
        for rel in relations:
            for tbl in (rel.src_table, rel.dst_table):
                if tbl:
                    rel_type_by_table.setdefault(tbl, rel.relation_type)
        for tbl in _tables_in_sql(sql_text, dialect=dialect):
            if tbl and tbl not in seen:
                seen.add(tbl)
                hits.append(dict(table=tbl, relation_type=rel_type_by_table.get(tbl, "SELECT"),
                                 source=_SRC_C,
                                 evidence_ref=f"{source_path} (template {template_id})"))
    return hits


def _tables_in_sql(sql_text: str, *, dialect: str = "oracle") -> list[str]:
    """直接从 sqlglot AST 抽所有真实表名(单表只读 SELECT 也覆盖)。解析失败 -> 空。"""
    try:
        tree = sqlglot.parse_one(preprocess_sql(sql_text), dialect=dialect)
    except Exception:
        return []
    if tree is None:
        return []
    cte_names = {c.alias for c in tree.find_all(exp.CTE)}
    out: list[str] = []
    seen_local: set[str] = set()
    for t in tree.find_all(exp.Table):
        name = t.name
        if not name or name.upper() in _SQL_KEYWORDS or name in cte_names or name in seen_local:
            continue
        seen_local.add(name)
        out.append(name)
    return out
