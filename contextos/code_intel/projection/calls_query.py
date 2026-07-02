"""callers/callees 遍历(spec D4 查询能力; 评分级联不在此, 等 09)。
两跳 = 逐层参数化 IN 查询(可移植, 不依赖方言递归 CTE), caps 全程生效(spec §9)。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection.method_resolve import resolve_bare_method_fqn

_EDGE_COLS = (S.code_calls.c.caller_method_fqn, S.code_calls.c.callee_method_fqn,
              S.code_calls.c.callee_class_fqn, S.code_calls.c.callee_method_name,
              S.code_calls.c.source_file, S.code_calls.c.line_no, S.code_calls.c.resolved)


def lookup_calls(engine: Engine, *, method_fqn: str, direction: str, depth: int,
                 fanout: int, max_rows: int) -> dict[str, Any]:
    if direction not in ("callers", "callees"):
        raise ValueError(f"direction must be callers|callees, got {direction!r}")
    if not 1 <= depth <= 2:
        raise ValueError(f"depth must be 1..2, got {depth}")

    edges: list[dict[str, Any]] = []
    truncated = False
    with engine.connect() as conn:
        # Bare seed (no signature segment) -> qualified form, since code_calls FQNs
        # always carry signatures. Unknown bare keeps input as-is (empty edges, no
        # error = today's behavior); ambiguous raises AmbiguousMethodFqn to caller.
        seed = resolve_bare_method_fqn(conn, method_fqn) or method_fqn
        visited: set[str] = {seed}
        frontier = [seed]
        for _level in range(depth):
            if not frontier:
                break
            if len(edges) >= max_rows:
                # LOW-2: 配额耗尽但还有未展开的活前沿(且深度没走完)-> 下层边被
                # 静默砍掉了, 必须报 truncated; depth 耗尽正常走完不进这条路径。
                truncated = True
                break
            next_frontier: list[str] = []
            for node in frontier:
                if direction == "callees":
                    q = select(*_EDGE_COLS).where(
                        S.code_calls.c.caller_method_fqn == node)
                else:
                    q = select(*_EDGE_COLS).where(
                        S.code_calls.c.callee_method_fqn == node)
                # LOW-3: row_id 定序 -> 截断取哪几条边确定(不随底层返回顺序漂移)
                q = q.order_by(S.code_calls.c.row_id).limit(fanout + 1)
                rows = conn.execute(q).fetchall()
                if len(rows) > fanout:
                    truncated = True
                    rows = rows[:fanout]
                for r in rows:
                    if len(edges) >= max_rows:
                        truncated = True
                        break
                    edges.append({
                        "caller_method_fqn": r.caller_method_fqn,
                        "callee_method_fqn": r.callee_method_fqn,
                        "callee_class_fqn": r.callee_class_fqn,
                        "callee_method_name": r.callee_method_name,
                        "source_file": r.source_file, "line_no": int(r.line_no or 0),
                        "resolved": bool(r.resolved)})
                    nxt = (r.callee_method_fqn if direction == "callees"
                           else r.caller_method_fqn)
                    if nxt and nxt not in visited:
                        visited.add(nxt)
                        next_frontier.append(nxt)
            frontier = next_frontier
    return {"edges": edges, "truncated": truncated, "direction": direction,
            "depth": depth, "root": seed}
