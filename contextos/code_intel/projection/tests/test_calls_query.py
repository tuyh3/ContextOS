"""lookup_calls: callees/callers 一跳与两跳 / fanout cap / max_rows cap + truncated /
depth 越界拒 / 两跳去环(A->B->A 不无限)。"""
from __future__ import annotations

import pytest

from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection import store
from contextos.code_intel.projection.calls_query import lookup_calls
from contextos.code_intel.projection.method_resolve import AmbiguousMethodFqn


def _method(i: int, cls: str, name: str, fqn: str) -> dict:
    return {"method_id": f"m{i}", "class_fqn": cls, "method_name": name,
            "name_lower": name.lower(), "method_fqn": fqn,
            "source_file": "src/X.java", "start_line": 1, "end_line": 2}


def _call(i: int, caller: str, callee_fqn: str) -> dict:
    cls = callee_fqn.rsplit(".", 2)[0] if callee_fqn.count(".") >= 2 else ""
    name = callee_fqn.rsplit(".", 1)[-1].split("(")[0]
    return {"call_id": f"k{i}", "caller_method_fqn": caller,
            "callee_method_fqn": callee_fqn,
            "callee_class_fqn": cls,
            "callee_method_name": name,
            "resolved": 1, "source_file": "src/X.java", "line_no": i}


@pytest.fixture
def graph(engine):
    """a -> b -> c; d -> b。"""
    S.ensure_projection_schema(engine)
    store.replace_all(engine, {"code_calls": [
        _call(1, "com.x.A.a()", "com.x.B.b()"),
        _call(2, "com.x.B.b()", "com.x.C.c()"),
        _call(3, "com.x.D.d()", "com.x.B.b()")]})
    return engine


def test_callees_depth1(graph):
    r = lookup_calls(graph, method_fqn="com.x.A.a()", direction="callees", depth=1,
                     fanout=200, max_rows=1000)
    assert [e["callee_method_fqn"] for e in r["edges"]] == ["com.x.B.b()"]
    assert r["truncated"] is False
    assert r["direction"] == "callees" and r["root"] == "com.x.A.a()"


def test_callees_depth2(graph):
    r = lookup_calls(graph, method_fqn="com.x.A.a()", direction="callees", depth=2,
                     fanout=200, max_rows=1000)
    assert {e["callee_method_fqn"] for e in r["edges"]} == {"com.x.B.b()", "com.x.C.c()"}


def test_callers_depth1(graph):
    r = lookup_calls(graph, method_fqn="com.x.B.b()", direction="callers", depth=1,
                     fanout=200, max_rows=1000)
    assert {e["caller_method_fqn"] for e in r["edges"]} == {"com.x.A.a()", "com.x.D.d()"}


def test_caps_truncate(graph):
    store.replace_all(graph, {"code_calls": [
        _call(i, "com.x.A.a()", f"com.x.T{i}.t()") for i in range(50)]})
    r = lookup_calls(graph, method_fqn="com.x.A.a()", direction="callees", depth=1,
                     fanout=10, max_rows=10)
    assert len(r["edges"]) == 10
    assert r["truncated"] is True


def test_max_rows_across_levels(graph):
    """两跳总量被 max_rows 截住。"""
    rows = [_call(i, "com.x.A.a()", f"com.x.M{i}.m()") for i in range(5)]
    rows += [_call(100 + i, f"com.x.M{i}.m()", f"com.x.N{i}.n()") for i in range(5)]
    store.replace_all(graph, {"code_calls": rows})
    r = lookup_calls(graph, method_fqn="com.x.A.a()", direction="callees", depth=2,
                     fanout=200, max_rows=7)
    assert len(r["edges"]) == 7
    assert r["truncated"] is True


def test_truncated_true_when_quota_exhausted_with_live_frontier(graph):
    """LOW-2 回归: level1 恰好填满 max_rows, depth=2 还有未展开的活前沿 ->
    truncated 必须 True(否则下层边静默缺失还报"完整")。"""
    rows = [_call(i, "com.x.A.a()", f"com.x.M{i}.m()") for i in range(5)]
    rows += [_call(100, "com.x.M0.m()", "com.x.N0.n()")]   # 下层确有边
    store.replace_all(graph, {"code_calls": rows})
    r = lookup_calls(graph, method_fqn="com.x.A.a()", direction="callees", depth=2,
                     fanout=200, max_rows=5)
    assert len(r["edges"]) == 5
    assert r["truncated"] is True


def test_truncated_false_when_depth_done_exactly_at_max_rows(graph):
    """depth 耗尽正常走完, 恰好 == max_rows 且无下层(fanout 也没溢出)->
    truncated False(没截掉任何东西, 别谎报截断)。"""
    store.replace_all(graph, {"code_calls": [
        _call(i, "com.x.A.a()", f"com.x.M{i}.m()") for i in range(5)]})
    r = lookup_calls(graph, method_fqn="com.x.A.a()", direction="callees", depth=1,
                     fanout=200, max_rows=5)
    assert len(r["edges"]) == 5
    assert r["truncated"] is False


def test_cycle_does_not_duplicate(graph):
    store.replace_all(graph, {"code_calls": [
        _call(1, "com.x.A.a()", "com.x.B.b()"),
        _call(2, "com.x.B.b()", "com.x.A.a()")]})
    r = lookup_calls(graph, method_fqn="com.x.A.a()", direction="callees", depth=2,
                     fanout=200, max_rows=1000)
    # A->B 与 B->A 两条边各一次, 不因环爆炸
    assert len(r["edges"]) == 2


def test_bare_seed_resolves_to_signature_qualified(graph):
    """裸种子 'com.x.A.a' 经 code_methods 解到 'com.x.A.a()' -> 找到边, root=带签名形态。"""
    with graph.begin() as conn:
        store.insert_rows_conn(conn, {"code_methods": [
            _method(1, "com.x.A", "a", "com.x.A.a()")]})
    r = lookup_calls(graph, method_fqn="com.x.A.a", direction="callees", depth=1,
                     fanout=200, max_rows=1000)
    assert [e["callee_method_fqn"] for e in r["edges"]] == ["com.x.B.b()"]
    assert r["root"] == "com.x.A.a()"


def test_bare_seed_ambiguous_raises(graph):
    """裸种子命中多重载 -> AmbiguousMethodFqn 透传给调用方。"""
    with graph.begin() as conn:
        store.insert_rows_conn(conn, {"code_methods": [
            _method(1, "com.x.A", "a", "com.x.A.a()"),
            _method(2, "com.x.A", "a", "com.x.A.a(int)")]})
    with pytest.raises(AmbiguousMethodFqn):
        lookup_calls(graph, method_fqn="com.x.A.a", direction="callees", depth=1,
                     fanout=200, max_rows=1000)


def test_bare_unknown_seed_keeps_input_empty_edges(graph):
    """裸但未知的种子: 不报错, 维持今日行为(空边 + root=原输入)。"""
    r = lookup_calls(graph, method_fqn="com.x.Z.z", direction="callees", depth=1,
                     fanout=200, max_rows=1000)
    assert r["edges"] == []
    assert r["root"] == "com.x.Z.z"


def test_depth_and_direction_bounds(graph):
    with pytest.raises(ValueError):
        lookup_calls(graph, method_fqn="x", direction="callees", depth=3,
                     fanout=10, max_rows=10)
    with pytest.raises(ValueError):
        lookup_calls(graph, method_fqn="x", direction="sideways", depth=1,
                     fanout=10, max_rows=10)
