"""search_code_query tool 适配测试(Plan 10 Task 6)。

tool 形态 = 单 query 字符串查符号,返回纯 dict list(给 MCP/CLI/lib 共用),
区别于吃 RequirementBreakdown 的 provider(provider.py)。复用 find_seeds,
把 query 包成 SearchTerm。FakeSearcher 沿用 test_seeds.py 的 canned-symbol 风格。
"""
from contextos.code_intel.code_search.tools import search_code_query


class FakeSearcher:
    """按 query 串返回 canned 符号(= JdtlsAdapter.request_workspace_symbol)。"""

    def __init__(self, table):
        self.table = table  # {query: [symbol_dict, ...]}
        self.queries = []

    def request_workspace_symbol(self, query):
        self.queries.append(query)
        return list(self.table.get(query, []))


def _sym(name, kind, rel, line_start, line_end, container=None):
    loc = {"relativePath": rel, "uri": f"file://{rel}",
           "range": {"start": {"line": line_start, "character": 0},
                     "end": {"line": line_end, "character": 0}}}
    d = {"name": name, "kind": kind, "location": loc}
    if container is not None:
        d["containerName"] = container
    return d


def test_returns_tool_dict_shape():
    """命中 -> [{target, kind, score, file, name_match}] 纯 dict 形态。"""
    s = FakeSearcher({
        "OrderService": [
            _sym("OrderService", 5, "app/OrderService.java", 10, 200, container="app.svc"),
        ],
    })
    rows = search_code_query(s, query="OrderService")
    assert isinstance(rows, list)
    assert len(rows) == 1
    r = rows[0]
    assert set(r.keys()) == {"target", "kind", "score", "file", "name_match"}
    assert r["target"] == "app.svc.OrderService"
    assert r["kind"] == "CLASS"
    assert r["file"] == "app/OrderService.java"
    # 精确同名 -> name_match 1.0;score 复用同一强度(本 tool 不叠 source_confidence)
    assert r["name_match"] == 1.0
    assert r["score"] == 1.0
    # 返回值是 JSON 友好的纯标量(非 pydantic / 非 dataclass)
    assert all(isinstance(v, (str, float, int)) for v in r.values())


def test_fuzzy_match_scores_point_six():
    s = FakeSearcher({"Order": [_sym("OrderServiceImpl", 5, "a/B.java", 1, 2)]})
    rows = search_code_query(s, query="Order")
    assert rows[0]["name_match"] == 0.6
    assert rows[0]["score"] == 0.6


def test_empty_query_returns_empty_safely():
    """空 query 不查 / 不抛,返回 []。"""
    s = FakeSearcher({"X": [_sym("X", 5, "a/B.java", 1, 1)]})
    assert search_code_query(s, query="") == []
    assert search_code_query(s, query="   ") == []
    assert s.queries == []  # 空 query 完全不打 JDT


def test_no_hit_returns_empty():
    s = FakeSearcher({})
    assert search_code_query(s, query="Nope") == []


def test_kind_filter_keeps_only_matching_kind():
    """kind 非空 -> 只保留该 01-Kind 的候选(METHOD/CLASS/...)。"""
    s = FakeSearcher({
        "feature": [
            _sym("featureFlag", 6, "a/B.java", 5, 9, container="a.B"),       # METHOD
            _sym("FeatureConf", 5, "a/C.java", 1, 9, container="a"),          # CLASS
        ],
    })
    methods = search_code_query(s, query="feature", kind="METHOD")
    assert [r["target"] for r in methods] == ["a.B.featureFlag"]
    classes = search_code_query(s, query="feature", kind="CLASS")
    assert [r["target"] for r in classes] == ["a.FeatureConf"]
    # kind="" -> 不过滤,两个都在
    both = search_code_query(s, query="feature")
    assert len(both) == 2
