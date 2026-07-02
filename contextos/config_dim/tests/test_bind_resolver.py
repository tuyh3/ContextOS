from contextos.config_dim.bind_resolver import resolve_bindings, Binding
from contextos.config_dim.extract import ConfigRef


class FakeSearcher:
    def __init__(self, by_name): self._m = by_name
    def request_workspace_symbol(self, query):  # 形参名对齐 SymbolSearcher Protocol(pyright clean)
        return self._m.get(query, [])


def _ref(key, rtype, fqn, path="com/x/offer/OfferConfig.java", line=5):
    return ConfigRef(key, rtype, path, line, fqn)


def test_exact_key_then_class_binding_verified_by_path():
    entities = [{"entity_id": "e1", "entity_key": "jdbc.url"}]
    refs = [_ref("jdbc.url", "annotation_value", "com.x.offer.OfferConfig")]
    searcher = FakeSearcher({"OfferConfig": [
        {"name": "OfferConfig", "location": {"relativePath": "com/x/offer/OfferConfig.java"}}
    ]})
    bindings = resolve_bindings(refs, entities, searcher=searcher)
    b = bindings[0]
    assert b.entity_id == "e1" and b.bind_type == "java_class"
    assert b.bind_target == "com.x.offer.OfferConfig"
    assert b.bind_strategy == "exact_match" and b.confidence == "high"


def test_multi_hit_no_path_match_degrades():
    # 同名类跨模块, workspaceSymbol 多命中且无路径一致项 -> source_file binding / needs_review
    entities = [{"entity_id": "e1", "entity_key": "jdbc.url"}]
    refs = [_ref("jdbc.url", "annotation_value", "com.x.offer.OfferConfig")]
    searcher = FakeSearcher({"OfferConfig": [
        {"name": "OfferConfig", "location": {"relativePath": "modA/Other.java"}},
        {"name": "OfferConfig", "location": {"relativePath": "modB/Another.java"}},
    ]})
    b = resolve_bindings(refs, entities, searcher=searcher)[0]
    assert b.bind_type == "source_file" or b.confidence == "needs_review"


def test_annotation_prefix_match_cb_strategy():
    # @Configuration("offer-switch") 前缀反向匹配 entity offer-switch.enable
    entities = [{"entity_id": "e2", "entity_key": "offer-switch.enable"}]
    refs = [_ref("offer-switch", "annotation", "com.x.offer.OfferConfig")]
    b = resolve_bindings(refs, entities, searcher=None)[0]
    assert b.entity_id == "e2" and b.bind_strategy == "annotation_prefix_match"


def test_resolve_bindings_builds_entity_index_once(monkeypatch):
    """entities 的排序/索引只依赖 entities, 应在 ref 循环外只算一次, 不随 ref 数线性重复。

    设计思路(memory feedback_contextos_test_documentation): profile 实测 —— 批量 insert 之后,
    config 维新瓶颈是 _match_entity 每个 ref 都重排一遍全部 entity(O(ref x entity log))。
    守护: 数 bind_resolver 模块内 sorted 的调用次数, 多 ref 下也应 <=1(不变量已提到循环外)。
    评分: 50 个不匹配 ref + 20 个 entity, 提升后 sorted 仅 1 次(逐 ref 实现会是 50 次)。
    """
    import contextos.config_dim.bind_resolver as br

    entities = [{"entity_id": f"e{i}", "entity_key": f"k.{i}"} for i in range(20)]
    refs = [_ref(f"nomatch.{i}", "method_arg", "com.x.P") for i in range(50)]
    real_sorted = sorted
    n = {"v": 0}
    monkeypatch.setattr(
        br, "sorted",
        lambda *a, **k: (n.__setitem__("v", n["v"] + 1), real_sorted(*a, **k))[1],
        raising=False)
    resolve_bindings(refs, entities)
    assert n["v"] <= 1, f"entities 排序了 {n['v']} 次, 应 <=1(不变量未提到 ref 循环外)"


def test_index_query_primitives():
    """#3b 索引查询基元: descendants(ek 以 key+'.' 为前缀的子键) / ancestors(ek 是 key 点前缀的
    祖先, 最长优先)。把 O(ref x entity) 线性 startswith 扫降成索引查。"""
    from contextos.config_dim.bind_resolver import _ancestors, _build_entity_index, _descendants
    ents = [{"entity_id": "e1", "entity_key": "a.b"},
            {"entity_id": "e2", "entity_key": "a.b.c"},
            {"entity_id": "e3", "entity_key": "x.y"}]
    keys, fbk, sk = _build_entity_index(ents)
    assert keys["a.b"]["entity_id"] == "e1"
    assert {e["entity_key"] for e in _descendants("a.b", fbk, sk)} == {"a.b.c"}
    assert [e["entity_key"] for e in _ancestors("a.b.c.d", fbk)] == ["a.b.c", "a.b"]
    assert _descendants("zzz", fbk, sk) == [] and _ancestors("zzz", fbk) == []


def test_hierarchical_picks_most_specific_ancestor():
    """多个祖先候选取最具体(最长)那个 —— 锁 #3b 索引化与原线性扫的 tie-break 等价。"""
    entities = [{"entity_id": "e1", "entity_key": "a"}, {"entity_id": "e2", "entity_key": "a.b"}]
    b = resolve_bindings([_ref("a.b.c", "method_arg", "com.x.P")], entities)[0]
    assert b.entity_id == "e2" and b.bind_strategy == "hierarchical_match"
