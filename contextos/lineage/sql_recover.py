"""Layer 4: .sql 文件双层恢复(移植 LP sql_recover.py)。

主路径 sqlglot.parse(oracle) 整文件 statement 分割;
fallback 按 ';' 切 + 去 SQL*Plus 噪声(REM/PROMPT/SET/SPOOL/EXIT/QUIT/WHENEVER/@@)。
"""
from __future__ import annotations

import re

import sqlglot

from contextos.lineage.models import RecoveredSqlCandidate, SourceFile

_NOISE_RE = re.compile(
    r"^(--|#|REM\b|PROMPT\b|SET\s|SPOOL\b|EXIT\b|QUIT\b|WHENEVER\b|@@)",
    re.IGNORECASE,
)


def recover_from_sql_file(source: SourceFile, *, dialect: str = "oracle",
                          ) -> list[RecoveredSqlCandidate]:
    content = source.content.strip()
    if not content:
        return []
    confidence = "medium" if source.category == "dao_sql" else "low"
    # 主路径: sqlglot 整体 parse(dialect 单一取值点, spec 4.5; 默认 oracle 向后兼容)
    try:
        statements = sqlglot.parse(content, dialect=dialect)
        results, line_offset, got_any = [], 1, False
        for stmt in statements:
            if stmt is None:
                continue
            sql_text = stmt.sql(dialect=dialect)
            if sql_text.strip():
                got_any = True
                results.append(RecoveredSqlCandidate(
                    source_path=source.path, line_start=line_offset,
                    line_end=line_offset + sql_text.count("\n"), container="",
                    sql_text=sql_text, recovery_mode="sql_file", confidence=confidence))
            line_offset += sql_text.count("\n") + 1
        if got_any:
            return results
    except Exception:
        pass
    # fallback: 分号切
    return _recover_by_semicolon(content, source.path)


def _recover_by_semicolon(content: str, source_path: str) -> list[RecoveredSqlCandidate]:
    cleaned_lines = []
    for line in content.split("\n"):
        stripped = line.strip()
        cleaned_lines.append("" if (not stripped or _NOISE_RE.match(stripped)) else line)
    cleaned = "\n".join(cleaned_lines)
    results, line_offset = [], 1
    for chunk in cleaned.split(";"):
        sql = re.sub(r"/\*.*?\*/", " ", chunk.strip(), flags=re.DOTALL).strip()
        if not sql or len(sql) < 10:
            line_offset += chunk.count("\n") + 1
            continue
        upper = sql.upper()
        if not any(kw in upper for kw in
                   ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "WITH")):
            line_offset += chunk.count("\n") + 1
            continue
        results.append(RecoveredSqlCandidate(
            source_path=source_path, line_start=line_offset,
            line_end=line_offset + sql.count("\n"), container="",
            sql_text=sql, recovery_mode="semicolon_split", confidence="low"))
        line_offset += chunk.count("\n") + 1
    return results
