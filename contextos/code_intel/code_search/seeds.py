"""workspaceSymbol 找种子(04 §3 第1步)。

把 02 给的 search_terms 撞上真实 Java 符号:"被命名为 X" 远比 "提到 X" 靠谱
(04 §1)。每个 term 问一次 workspaceSymbol;精确同名 = 1.0,模糊命中 = 0.6
(04 §7 name_match_strength)。SymbolKind -> 01 §3.1 Kind 映射。

纯逻辑:只依赖 SymbolSearcher Protocol(= JdtlsAdapter.request_workspace_symbol),
不 import vendored solidlsp,单测用 FakeSearcher。
"""
from __future__ import annotations

from typing import Any, Protocol

from contextos.code_intel.code_search.query_expand import expand_search_term
from contextos.code_intel.code_search.schema import CodeSearchSignals, SearchTerm
from contextos.orchestrator.provider_io import ProviderCandidate


class SymbolSearcher(Protocol):
    def request_workspace_symbol(self, query: str) -> list[Any]: ...


# LSP SymbolKind int -> 01 §3.1 Kind(见 solidlsp lsp_protocol_handler/lsp_types.py
# SymbolKind:Class=5 Constructor=9 Enum=10 Interface=11 Method=6 Field=8 Constant=14)。
# 缺省 / 其它(Variable=13 等)-> OTHER。
_KIND_MAP: dict[int, str] = {
    5: "CLASS",
    10: "CLASS",       # Enum -> CLASS(01 无 ENUM 取值)
    11: "INTERFACE",
    6: "METHOD",
    9: "METHOD",       # Constructor -> METHOD
    8: "FIELD",
    14: "FIELD",       # Constant -> FIELD
}


def _symbol_kind_to_kind(raw: Any) -> str:
    try:
        return _KIND_MAP.get(int(raw), "OTHER")
    except (TypeError, ValueError):
        return "OTHER"


def _fqn(sym: dict[str, Any]) -> str:
    container = sym.get("containerName") or ""
    name = sym.get("name", "")
    return f"{container}.{name}" if container else name


def find_seeds(searcher: SymbolSearcher, terms: list[SearchTerm]) -> list[ProviderCandidate]:
    """workspaceSymbol 种子搜索。每个原词扩成多个查询串(query_expand:救 02 复合名
    撞不上真符号),逐串查;命中强度对【原词】算(经子查询找到的算 0.6,不虚高)。
    同 target 去重,保留 name_match_strength 更高者。"""
    seen: dict[str, ProviderCandidate] = {}
    for st in terms:
        for query in expand_search_term(st.term):
            for sym in searcher.request_workspace_symbol(query):
                name = sym.get("name", "")
                if not name:
                    continue  # 防御:无名符号会在 dedup 里以 "" 撞 key 静默丢数据(code review I-1)
                loc = sym.get("location") or {}
                rng = loc.get("range") or {}
                start = rng.get("start") or {}
                end = rng.get("end") or {}
                # 强度对【原词】算, 不是当前 query 串 —— 经扩展前缀找到的算模糊 0.6,
                # 不因前缀恰好 == 某符号名而虚高成 1.0(原词才是用户给的)。
                strength = 1.0 if name == st.term else 0.6
                signals = CodeSearchSignals(
                    name_match_strength=strength,
                    call_distance_from_seed=0,
                    call_direction="seed",
                    binding_source="jdt-ls",
                    file=loc.get("relativePath") or loc.get("uri") or "",
                    line_start=int(start.get("line", -1)),
                    line_end=int(end.get("line", -1)),
                )
                target = _fqn(sym)
                candidate = ProviderCandidate(
                    target=target,
                    kind=_symbol_kind_to_kind(sym.get("kind")),
                    signals=signals.model_dump(),
                )
                prev = seen.get(target)
                if prev is None or prev.signals["name_match_strength"] < strength:
                    seen[target] = candidate
    return list(seen.values())
