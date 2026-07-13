"""配置表识别(四路融合)+ rule_sets Scope A。

设计契约: 06-配置维度/design.md §5(四路融合识别配置表)+ §3(rule_sets)。
spec §5.1 path A(0.10/low 表名启发 + 通用中立 seed)/ path B(DDL 表注释, store table_metadata.comment)/
path C(RAG 业务文档 cheap-first sparse, 无 LLM)/ path D(客户字典)+ §5.5 融合阈值。

注: 取数侧(注释 / search)由 pipeline 注入, 本模块只做纯逻辑便于单测。
- path B(D.5 去 live SQL 化): 注释来自 store 已刷新的 table_metadata.comment(各方言同一列,
  天然方言无关), 不再直发 ALL_TAB_COMMENTS, 不再需要 execute_query 通道。
- path C/D 的 search 走 03b sparse + corpus_scope, cheap-first 无 LLM(LLM 语义判定 09 eval-gated)。
"""
from __future__ import annotations

import tomllib
from pathlib import Path

# 通用中立 seed: contextos/config_dim/identify.py -> parents[2] = worktree 根
_SEED = Path(__file__).resolve().parents[2] / "data" / "config_dim" / "name_patterns.default.toml"


def load_default_name_patterns() -> list[str]:
    """通用中立表名启发 seed(跨域, 无客户业务词)。客户用 profile.config_tables.detection.name_patterns 叠加。"""
    if _SEED.exists():
        return tomllib.loads(_SEED.read_text("utf-8")).get("name_patterns", [])
    return []


# --- path A: 表名启发 + 规则列 ---

def path_a_score(table_name: str, columns: list[str], name_patterns: list[str],
                 rule_columns: set[str]) -> tuple[float, dict]:
    """表名命中 name_patterns 或 >=2 规则列 -> 命中信号(1.0); 融合层(fuse)乘权重 0.10。"""
    tn = (table_name or "").upper()
    name_hit = any(p.upper() in tn for p in name_patterns)
    rc_hit = sum(1 for c in columns if c.upper() in {r.upper() for r in rule_columns})
    ev = {"name_hit": name_hit, "rule_columns_hit": rc_hit}
    if name_hit or rc_hit >= 2:
        return (1.0, ev)
    return (0.0, ev)


# --- path B: DDL 表注释(去 live SQL 化, spec 附录 D.5) ---

def path_b_from_comment(comment: str | None, kw_zh: list[str], kw_en: list[str]) -> dict | None:
    """DDL 表注释含配置信号词 -> 命中(high)。

    D.5(冷评审 M1): 注释来自 store 已刷新的 `table_metadata.comment`(MetadataProvider 各方言
    都填同一列), **不再**直发 `ALL_TAB_COMMENTS`(Oracle 字典视图), 因此天然方言无关, 也不再
    需要 execute_query 通道 —— 随 live SQL 一并消除了注入面。识别逻辑与旧路 B 一致(注释 LIKE
    配置关键词), 复用 has_config_signal 信号词判定(与 path C/D 同一 predicate)。"""
    cmt = (comment or "").strip()
    if cmt and has_config_signal(cmt, kw_zh, kw_en):
        return {"confidence": "high", "excerpt": cmt[:200], "path": "B"}
    return None


# --- path C: RAG 业务文档(cheap-first sparse + 关键词信号, 无 LLM) ---

def has_config_signal(text: str, kw_zh: list[str], kw_en: list[str]) -> bool:
    """文本含配置信号词(zh 直配 / en 小写)。cheap-first 字面判定, 不调 LLM。"""
    t = text or ""
    tl = t.lower()
    return any(k in t for k in kw_zh) or any(k.lower() in tl for k in kw_en)


def path_c_query(table_name: str, search, kw_zh: list[str], kw_en: list[str]) -> dict | None:
    """search(patterns, subsets=['business_docs','dict_docs']) -> hits(每 hit 有 .line/.rel_path)。
    cheap-first: 字面命中表名 + 行含配置信号词 -> 候选。无 LLM(LLM 语义判定 09 eval-gated)。"""
    hits = search([table_name], ["business_docs", "dict_docs"]) or []
    for h in hits:
        line = getattr(h, "line", "")
        if has_config_signal(line, kw_zh, kw_en):
            # cheap-first sparse 无 relevance 分 -> 保守标 medium(design §5.3 high 留 09 LLM eval-gated)
            return {"confidence": "medium", "excerpt": line[:200], "path": "C",
                    "evidence_ref": getattr(h, "rel_path", "")}
    return None


# --- path D: 客户字典(customer_dict corpus, 同 path C 机制换子集) ---

def path_d_query(table_name: str, search, kw_zh, kw_en) -> dict | None:
    """客户字典(customer_dict corpus)。同 path C 机制, 换子集; 客户无字典 -> search 返空 -> None。"""
    hits = search([table_name], ["customer_dict"]) or []
    for h in hits:
        line = getattr(h, "line", "")
        if has_config_signal(line, kw_zh, kw_en):
            return {"confidence": "high", "excerpt": line[:200], "path": "D",
                    "evidence_ref": getattr(h, "rel_path", "")}
    return None


# --- 四路融合 ---

def fuse_config_table(path_a: float, path_b: float, path_c: float, path_d: float,
                      weights=(0.40, 0.30, 0.20, 0.10)) -> dict:
    """权重 .4B+.3C+.2D+.1A; >=0.6 且 >=2 路 -> high; 0.3-0.6 -> needs_review; <0.3 -> skip。
    权重初值, 09 校准(spec 决策13)。"""
    wb, wc, wd, wa = weights
    score = round(wb * path_b + wc * path_c + wd * path_d + wa * path_a, 4)
    n_paths = sum(1 for x in (path_a, path_b, path_c, path_d) if x > 0)
    if score >= 0.6 and n_paths >= 2:
        verdict = "high"
    elif score >= 0.3:
        verdict = "needs_review"
    else:
        verdict = "skip"
    return {"score": score, "verdict": verdict, "n_paths": n_paths}


# --- rule_sets Scope A(表级规则识别) ---

def identify_rule_set(table_name: str, columns: list[str], rule_columns: set[str],
                      category_map: dict[str, str]) -> dict | None:
    """Scope A 表级: >=2 规则列 -> rule_set; category 由 profile category_map 名匹配推(非硬编码)。
    rule_clauses 行级 v2 不填(决策11)。"""
    rc_hit = sum(1 for c in columns if c.upper() in {r.upper() for r in rule_columns})
    if rc_hit < 2:
        return None
    tn = table_name.upper()
    category = next((cat for pat, cat in category_map.items() if pat.upper() in tn), "")
    return {"name": table_name, "category": category, "status": "active",
            "confidence": "medium", "rule_columns_hit": rc_hit}


def rule_bindings_for(rule_set_id: str, table: str, engine_05=None) -> list[dict]:
    """规则表 -> 代码(reuse 05 表->代码; engine_05 None 则空)。

    db_snapshot.table_to_code reuse 05 lineage_evidence(source_path)给 source_file 级,
    每命中源文件一条 bind_role='subject' 的 rule_binding。离线无 05 -> 空 list。"""
    if engine_05 is None:
        return []
    from contextos.config_dim.db_snapshot import table_to_code
    return [{"rule_set_id": rule_set_id, "bind_type": "source_file",
             "bind_target": r["source_file"], "bind_role": "subject"}
            for r in table_to_code(engine_05, table)]
