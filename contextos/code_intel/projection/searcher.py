"""ProjectionSearcher: 查表平替 workspaceSymbol(spec §6)。

实现 code_search.seeds.SymbolSearcher Protocol, 返回 LSP 形状 dict ->
find_seeds / query_expand / tools.search_code_query 零改动。
匹配语义: 精确(cs) / ci 同名 / 前缀 / 子串 —— 强度由 find_seeds 对原词算
(精确 1.0 其余 0.6), 这里只负责"找得到"; 但按匹配质量稳定排序再截断,
防 class 子串命中吞掉 field 精确命中(plan 二轮 review LOW)。
每表两段查询(T11 review HIGH-2): exact 段(精确/ci 同名)与 fuzzy 段(前缀/子串)
各自带 LIMIT —— 同表大量子串命中占满单一 LIMIT 时精确命中不被挤出。
query 中 `%`/`_`/`\\` 按字面匹配, 不当 LIKE 通配(T11 review MEDIUM-1)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import not_, or_, select
from sqlalchemy.engine import Engine

from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection import store

# LSP SymbolKind: Class=5 Method=6 Field=8 Constructor=9 Enum=10 Interface=11
_CLASS_KIND_TO_LSP = {"interface": 11, "enum": 10}


def _like_escape(s: str) -> str:
    """LIKE 模式转义: 先翻倍转义字符 `\\` 自身, 再转 `%`/`_` 为字面量。
    配合 like(..., escape="\\") 使用(SQLAlchemy 渲染 ESCAPE '\\')。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ProjectionMissingError(RuntimeError):
    """投影未 build(spec D3 诚实 miss): message 给修复动作。"""

    def __init__(self) -> None:
        super().__init__("code projection not built, run `contextos init`")


class ProjectionSearcher:
    def __init__(self, engine: Engine, *, per_query_cap: int = 100) -> None:
        self._engine = engine
        self._cap = per_query_cap
        self._checked = False

    def _ensure_built(self) -> None:
        if self._checked:
            return
        if not store.get_meta(self._engine, "projection_build_id"):
            raise ProjectionMissingError()
        self._checked = True

    def freshness(self) -> dict[str, str]:
        return {
            "projection_build_id": store.get_meta(self._engine, "projection_build_id") or "",
            "indexed_commit": store.get_meta(self._engine, "last_indexed_commit") or "",
            "projection_status": store.get_meta(self._engine, "build_status") or "",
        }

    def request_workspace_symbol(self, query: str) -> list[dict[str, Any]]:
        self._ensure_built()
        q = (query or "").strip()
        if not q:
            return []
        ql = q.lower()
        esc = _like_escape(ql)
        out: list[dict[str, Any]] = []
        with self._engine.connect() as conn:
            def _exact(name_col: Any, lower_col: Any) -> Any:
                return or_(name_col == q, lower_col == ql)

            def _fuzzy(name_col: Any, lower_col: Any) -> Any:
                # MEDIUM-1: 前缀/子串臂转义 LIKE 通配(查 MAX_ITEMS 不吃 MAXAITEMS,
                # 查 % 不变全表扫描); NOT exact 防两段重复取同一行。
                return (or_(lower_col.like(f"{esc}%", escape="\\"),
                            lower_col.like(f"%{esc}%", escape="\\"))
                        & not_(_exact(name_col, lower_col)))

            def _collect(cols: tuple[Any, ...], name_col: Any, lower_col: Any,
                         mk: Any) -> None:
                # HIGH-2: exact / fuzzy 两段各自独立 LIMIT —— 同表 150 个子串命中
                # 占满单一 LIMIT 时, 精确命中没有 cap 压力, 不会进不了结果。
                for cond in (_exact(name_col, lower_col), _fuzzy(name_col, lower_col)):
                    for row in conn.execute(select(*cols).where(cond).limit(self._cap)):
                        out.append(mk(row))

            _collect(
                (S.code_classes.c.class_name, S.code_classes.c.package_name,
                 S.code_classes.c.kind, S.code_classes.c.source_file,
                 S.code_classes.c.start_line, S.code_classes.c.end_line),
                S.code_classes.c.class_name, S.code_classes.c.name_lower,
                lambda row: _sym(row.class_name, row.package_name,
                                 _CLASS_KIND_TO_LSP.get(row.kind or "", 5),
                                 row.source_file, row.start_line, row.end_line))
            _collect(
                (S.code_methods.c.method_name, S.code_methods.c.class_fqn,
                 S.code_methods.c.is_constructor, S.code_methods.c.source_file,
                 S.code_methods.c.start_line, S.code_methods.c.end_line),
                S.code_methods.c.method_name, S.code_methods.c.name_lower,
                lambda row: _sym(row.method_name, row.class_fqn,
                                 9 if row.is_constructor else 6,
                                 row.source_file, row.start_line, row.end_line))
            _collect(
                (S.code_fields.c.field_name, S.code_fields.c.class_fqn,
                 S.code_fields.c.source_file, S.code_fields.c.start_line,
                 S.code_fields.c.end_line),
                S.code_fields.c.field_name, S.code_fields.c.name_lower,
                lambda row: _sym(row.field_name, row.class_fqn, 8,
                                 row.source_file, row.start_line, row.end_line))

        def _rank(name: str) -> int:
            nl = name.lower()
            if name == q:
                return 0
            if nl == ql:
                return 1
            if nl.startswith(ql):
                return 2
            return 3

        # 稳定排序: exact > ci > prefix > substring; 同 rank 内 (name, file) 定序
        # (LOW-3: 截断取哪几条不随底层返回顺序漂移)
        out.sort(key=lambda s: (_rank(s["name"]), s["name"],
                                s["location"]["relativePath"]))
        return out[: self._cap]


def _sym(name: str, container: str, kind: int, file: str,
         line_start: int, line_end: int) -> dict[str, Any]:
    return {
        "name": name, "containerName": container or "", "kind": kind,
        "location": {"relativePath": file,
                     "range": {"start": {"line": int(line_start if line_start is not None else -1)},
                               "end": {"line": int(line_end if line_end is not None else -1)}}},
    }
