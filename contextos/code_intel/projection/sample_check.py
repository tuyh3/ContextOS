"""JDT LS 抽样对照(spec §3.1 条件 3): 投影 vs live workspaceSymbol 存在性。
mismatch = 抽样里 JDT 找不到同名符号的比例(JDT 调用抛错按 miss 计)。
位置级比对 v1 不做(行号 0/1-based 与 includeSourceMethodDeclarations 表示差异易假阳)。
吃 Connection 不吃 Engine(第三轮 review HIGH): build 的单事务 staging 里调,
读到的是未 commit 的新行; 超阈由 build 回滚 = 真保旧。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from contextos.code_intel.projection import schema as S


def _sample_names(conn: Connection, name_col: Any, n: int) -> list[str]:
    if n <= 0:
        return []
    rows = conn.execute(select(name_col).order_by(func.random()).limit(n)).fetchall()
    return [r[0] for r in rows]


def sample_mismatch_ratio(conn: Connection, jdt_searcher: Any, *,
                          n_classes: int, n_methods: int) -> float:
    names = (_sample_names(conn, S.code_classes.c.class_name, n_classes)
             + _sample_names(conn, S.code_methods.c.method_name, n_methods))
    if not names:
        return 0.0
    misses = 0
    for name in names:
        try:
            syms = jdt_searcher.request_workspace_symbol(name)
        except Exception:  # noqa: BLE001  JDT 猝死/超时 = 对照不可信, 按 miss 计
            misses += 1
            continue
        if not any(s.get("name") == name for s in syms):
            misses += 1
    return misses / len(names)
