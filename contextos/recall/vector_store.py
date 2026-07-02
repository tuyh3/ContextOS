"""LanceDB wrapper for POC."""
from __future__ import annotations

from pathlib import Path

import lancedb
import numpy as np


class LanceVectorStore:
    """Simple LanceDB wrapper.

    Schema is inferred from the first row added. Expects rows shaped like:
      {"id": str, "text": str, "source": str, "vector": list[float] | ndarray, ...}
    """

    def __init__(self, path: str | Path, dim: int):
        self._db = lancedb.connect(str(path))
        self._dim = dim
        self._table = None
        # lancedb 0.10's list_tables() returns ListTablesResponse — must
        # access `.tables` for the actual list[str]. Earlier `list(...)`
        # wrapping was WRONG: it produced [('tables', ['asset']),
        # ('page_token', None)] (dict_items shape) and the 'asset' check
        # always failed, silently returning 0 hits on every query. Audit
        # 2026-05-28: caught in Task 7 A/B when both models reported 0.0
        # module_coverage on a known-good corpus.
        if "asset" in (self._db.list_tables().tables or []):
            self._table = self._db.open_table("asset")

    @staticmethod
    def _normalize(rows: list[dict]) -> list[dict]:
        out = []
        for r in rows:
            r2 = dict(r)
            v = r2.get("vector")
            # isinstance narrows for pyright; list[float] passes through.
            if isinstance(v, np.ndarray):
                r2["vector"] = v.tolist()
            out.append(r2)
        return out

    def add(self, rows: list[dict]) -> None:
        if not rows:
            return
        normalized = self._normalize(rows)
        if self._table is None:
            # First write — create table seeded with ALL rows in one shot.
            self._table = self._db.create_table(
                "asset", data=normalized, mode="overwrite"
            )
        else:
            self._table.add(normalized)

    def query(self, vector, top_k: int = 10) -> list[dict]:
        if self._table is None:
            if "asset" in (self._db.list_tables().tables or []):
                self._table = self._db.open_table("asset")
            else:
                return []
        v = vector.tolist() if hasattr(vector, "tolist") else vector
        df = self._table.search(v).limit(top_k).to_pandas()
        return df.to_dict("records")
