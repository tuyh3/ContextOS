"""Offline DDL parser for demoproj SQL files.

Used by:
- Task 10 candidate generation (always — keyword lookup on tables/columns)
- Task 8 fallback when test instances unreachable

POC version: regex-based. v1 should switch to SQLGlot for accuracy.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Column:
    name: str
    dtype: str
    comment: str = ""


@dataclass
class Table:
    name: str
    file: str
    columns: list[Column] = field(default_factory=list)
    comment: str = ""
    schema: str = ""  # owner / schema prefix when `CREATE TABLE SCHEMA.NAME (...)`


# The DDL dump for a large real customer project uses schema-qualified table names ~93% of the time
# (e.g. `CREATE TABLE RES.RES_PAYCARD_STORY_HIS (...)`). The optional non-capturing
# group `(?:["']?(\w+)["']?\.)?` matches the SCHEMA. prefix when present.
# group(1) = schema (or None), group(2) = bare table name.
_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+"
    r"(?:[\"']?(\w+)[\"']?\.)?"
    r"[\"']?(\w+)[\"']?\s*\(",
    re.IGNORECASE,
)
_COL_RE = re.compile(r"^\s*[\"']?(\w+)[\"']?\s+([A-Z]+(?:\(\d+(?:,\s*\d+)?\))?)", re.IGNORECASE)


class OfflineSchema:
    """Index of demoproj DDL CREATE TABLE statements parsed from .sql files.

    Lookup keys are SCHEMA.NAME (uppercase) when the DDL had a schema prefix,
    otherwise bare NAME. find_table() accepts either form for convenience.
    """

    def __init__(self, tables: dict[str, Table]):
        self._tables = tables

    @classmethod
    def from_directory(cls, ddl_dir: Path) -> "OfflineSchema":
        tables: dict[str, Table] = {}
        if not ddl_dir.exists():
            return cls(tables)
        for sql in ddl_dir.rglob("*.sql"):
            try:
                content = sql.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            # finditer (not search): some demoproj files bundle multiple CREATE TABLE
            # statements (e.g. QD.addTable.sql ships 15). The old `search` returned
            # only the first match and silently dropped the rest.
            for m in _TABLE_RE.finditer(content):
                tbl = cls._parse_one_table(content, m, str(sql))
                if tbl is None:
                    continue
                key = f"{tbl.schema}.{tbl.name}" if tbl.schema else tbl.name
                tables[key] = tbl
        return cls(tables)

    @staticmethod
    def _parse_one_table(content: str, m: "re.Match[str]", file: str) -> Table | None:
        schema = (m.group(1) or "").upper()
        name = m.group(2).upper()
        start = m.end()
        # Find the column-list closing `);` for THIS table. We bound the search to
        # the start of the next CREATE TABLE so a stray `);` from a later table
        # can't accidentally truncate this one's body.
        next_match = _TABLE_RE.search(content, start)
        upper = next_match.start() if next_match else len(content)
        end = content.find(");", start)
        if end < 0 or end > upper:
            end = upper
        body = content[start:end]
        cols = []
        for line in body.splitlines():
            cm = _COL_RE.match(line.strip().rstrip(","))
            if cm:
                cols.append(Column(name=cm.group(1).upper(), dtype=cm.group(2).upper()))
        return Table(name=name, schema=schema, file=file, columns=cols)

    def list_tables(self) -> list[str]:
        return sorted(self._tables.keys())

    def find_table(self, name: str) -> Table | None:
        """Lookup by SCHEMA.NAME (preferred) or bare NAME. Case-insensitive.

        If a bare NAME hits multiple schemas (same table name across schemas),
        returns the first one in sorted-key order — a quirk callers should know
        about. Use the qualified form to be precise.
        """
        n = name.upper()
        # Direct hit (either SCHEMA.NAME or bare NAME stored as key)
        direct = self._tables.get(n)
        if direct is not None:
            return direct
        # Bare name fallback: scan keys for `.NAME` suffix
        suffix = f".{n}"
        for key, tbl in sorted(self._tables.items()):
            if key.endswith(suffix):
                return tbl
        return None

    def find_tables_by_keyword(self, keyword: str) -> list[Table]:
        kw = keyword.lower()
        return [t for t in self._tables.values()
                if kw in t.name.lower() or any(kw in c.name.lower() for c in t.columns)]
