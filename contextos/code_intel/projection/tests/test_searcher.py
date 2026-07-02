"""ProjectionSearcher: LSP 形状返回(find_seeds 直接吃) / 三表查(class/method/field,
字段维白捡) / 精确+ci+前缀+子串 / rank 排序防 cap 吞 / 每查询 cap /
空投影抛 ProjectionMissingError / freshness() 透传 meta。"""
from __future__ import annotations

import pytest

from contextos.code_intel.code_search.schema import SearchTerm
from contextos.code_intel.code_search.seeds import find_seeds
from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection import store
from contextos.code_intel.projection.searcher import ProjectionMissingError, ProjectionSearcher


@pytest.fixture
def seeded(engine):
    S.ensure_projection_schema(engine)
    store.replace_all(engine, {
        "code_classes": [
            {"class_id": "c1", "class_fqn": "com.acme.OrderService", "class_name": "OrderService",
             "name_lower": "orderservice", "package_name": "com.acme", "kind": "class",
             "source_file": "src/OrderService.java", "start_line": 3, "end_line": 40}],
        "code_methods": [
            {"method_id": "m1", "class_fqn": "com.acme.OrderService", "method_name": "placeOrder",
             "name_lower": "placeorder", "method_fqn": "com.acme.OrderService.placeOrder(int)",
             "source_file": "src/OrderService.java", "start_line": 10, "end_line": 20}],
        "code_fields": [
            {"field_id": "f1", "class_fqn": "com.acme.OrderService", "field_name": "MAX_ITEMS",
             "name_lower": "max_items", "source_file": "src/OrderService.java",
             "start_line": 4, "end_line": 4}],
    })
    store.set_meta(engine, "projection_build_id", "b1")
    store.set_meta(engine, "last_indexed_commit", "c0ffee")
    store.set_meta(engine, "build_status", "ok")
    return engine


def test_lsp_shape_and_find_seeds_compat(seeded):
    s = ProjectionSearcher(seeded)
    cands = find_seeds(s, [SearchTerm(term="OrderService", kind="camelcase")])
    assert len(cands) == 1
    c = cands[0]
    assert c.target == "com.acme.OrderService"
    assert c.kind == "CLASS"
    assert c.signals["name_match_strength"] == 1.0      # 精确同名
    assert c.signals["file"] == "src/OrderService.java"
    assert c.signals["line_start"] == 3


def test_field_dimension_now_searchable(seeded):
    """workspaceSymbol 不返裸字段的缺口(2026-06-01 手测), 投影白捡(spec §6)。"""
    s = ProjectionSearcher(seeded)
    syms = s.request_workspace_symbol("MAX_ITEMS")
    assert [x["name"] for x in syms] == ["MAX_ITEMS"]
    assert syms[0]["kind"] == 8                          # LSP Field
    assert syms[0]["containerName"] == "com.acme.OrderService"


def test_method_kind_and_container(seeded):
    s = ProjectionSearcher(seeded)
    syms = s.request_workspace_symbol("placeOrder")
    assert syms[0]["kind"] == 6                          # LSP Method
    assert syms[0]["containerName"] == "com.acme.OrderService"
    rng = syms[0]["location"]["range"]
    assert rng["start"]["line"] == 10 and rng["end"]["line"] == 20


def test_ci_prefix_substring(seeded):
    s = ProjectionSearcher(seeded)
    assert s.request_workspace_symbol("orderservice")    # ci 同名
    assert s.request_workspace_symbol("Order")           # 前缀
    assert s.request_workspace_symbol("Service")         # 子串
    assert not s.request_workspace_symbol("Nope")
    assert not s.request_workspace_symbol("   ")


def test_per_query_cap(seeded):
    store.replace_all(seeded, {"code_classes": [
        {"class_id": f"c{i}", "class_fqn": f"com.acme.Foo{i}", "class_name": f"Foo{i}",
         "name_lower": f"foo{i}", "source_file": f"src/F{i}.java"} for i in range(120)]})
    s = ProjectionSearcher(seeded, per_query_cap=100)
    assert len(s.request_workspace_symbol("Foo")) == 100


def test_exact_field_survives_cap_crowding(seeded):
    """plan review LOW: 宽泛 query 下大量 class 前缀/子串命中不得吞掉 field 精确命中。"""
    store.replace_all(seeded, {
        "code_classes": [
            {"class_id": f"c{i}", "class_fqn": f"com.acme.MaximumA{i}",
             "class_name": f"MaximumA{i}", "name_lower": f"maximuma{i}",
             "source_file": f"src/M{i}.java"} for i in range(120)],
        "code_fields": [
            {"field_id": "f1", "class_fqn": "com.acme.K", "field_name": "MAXIMUM",
             "name_lower": "maximum", "source_file": "src/K.java"}]})
    s = ProjectionSearcher(seeded, per_query_cap=100)
    syms = s.request_workspace_symbol("maximum")
    assert syms[0]["name"] == "MAXIMUM"        # ci-exact 排最前, 不被 120 个前缀命中挤出


def test_exact_hit_immune_to_same_table_cap_crowding(seeded):
    """HIGH-2 回归: 同表 150 个子串命中先占满 SQL LIMIT 时, 精确命中不得被挤出
    (exact 段独立查询, 不与 fuzzy 段争同一 LIMIT)。"""
    store.replace_all(seeded, {"code_classes": [
        {"class_id": f"c{i}", "class_fqn": f"com.acme.FooOrder{i}",
         "class_name": f"FooOrder{i}", "name_lower": f"fooorder{i}",
         "source_file": f"src/F{i}.java"} for i in range(150)] + [
        {"class_id": "cx", "class_fqn": "com.acme.Order", "class_name": "Order",
         "name_lower": "order", "source_file": "src/Order.java"}]})
    s = ProjectionSearcher(seeded, per_query_cap=100)
    syms = s.request_workspace_symbol("Order")
    assert syms[0]["name"] == "Order"        # 精确命中在场且排第一
    assert len(syms) == 100                  # cap 仍然生效


def test_like_wildcards_escaped(seeded):
    """MEDIUM-1 回归: query 里的 `_`/`%` 是字面量, 不是 LIKE 通配。"""
    store.replace_all(seeded, {"code_fields": [
        {"field_id": "f1", "class_fqn": "com.acme.K", "field_name": "MAX_ITEMS",
         "name_lower": "max_items", "source_file": "src/K.java"},
        {"field_id": "f2", "class_fqn": "com.acme.K", "field_name": "MAXAITEMS",
         "name_lower": "maxaitems", "source_file": "src/K.java"}]})
    s = ProjectionSearcher(seeded)
    names = [x["name"] for x in s.request_workspace_symbol("MAX_ITEMS")]
    assert names == ["MAX_ITEMS"]                  # `_` 不当单字符通配吃 MAXAITEMS
    assert s.request_workspace_symbol("%") == []   # `%` 不当全表通配


def test_empty_projection_raises(engine):
    S.ensure_projection_schema(engine)
    with pytest.raises(ProjectionMissingError, match="contextos init"):
        ProjectionSearcher(engine).request_workspace_symbol("Anything")


def test_freshness(seeded):
    f = ProjectionSearcher(seeded).freshness()
    assert f == {"projection_build_id": "b1", "indexed_commit": "c0ffee",
                 "projection_status": "ok"}
