"""workspaceSymbol 种子搜索测试。FakeSearcher 喂 canned UnifiedSymbolInformation。"""
from contextos.code_intel.code_search.schema import SearchTerm


class FakeSearcher:
    """按 term 返回 canned 符号。模拟 JdtlsAdapter.request_workspace_symbol。"""

    def __init__(self, table):
        self.table = table  # {term: [symbol_dict, ...]}
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


def test_exact_name_match_strength_one_and_kind_mapping():
    from contextos.code_intel.code_search.seeds import find_seeds
    s = FakeSearcher({
        "DynamicChargingSVImpl": [
            _sym("DynamicChargingSVImpl", 5, "order/.../DynamicChargingSVImpl.java", 10, 200,
                 container="order.soa.biz.orderservice.impl"),
        ],
    })
    seeds = find_seeds(s, [SearchTerm(term="DynamicChargingSVImpl", kind="camelcase")])
    assert len(seeds) == 1
    c = seeds[0]
    assert c.target == "order.soa.biz.orderservice.impl.DynamicChargingSVImpl"
    assert c.kind == "CLASS"
    assert c.signals["name_match_strength"] == 1.0
    assert c.signals["call_direction"] == "seed"
    assert c.signals["call_distance_from_seed"] == 0
    assert c.signals["binding_source"] == "jdt-ls"
    assert c.signals["file"] == "order/.../DynamicChargingSVImpl.java"
    assert c.signals["line_start"] == 10
    assert c.signals["line_end"] == 200


def test_fuzzy_match_strength_point_six():
    from contextos.code_intel.code_search.seeds import find_seeds
    s = FakeSearcher({
        "Charging": [_sym("DynamicChargingSVImpl", 5, "a/B.java", 1, 2)],
    })
    seeds = find_seeds(s, [SearchTerm(term="Charging", kind="other")])
    assert seeds[0].signals["name_match_strength"] == 0.6


def test_method_and_field_and_interface_kind_mapping():
    from contextos.code_intel.code_search.seeds import find_seeds
    s = FakeSearcher({
        "batchStart": [_sym("batchStart", 6, "a/B.java", 5, 9, container="a.B")],
        "MAX": [_sym("MAX", 8, "a/B.java", 3, 3, container="a.B")],
        "IDynamicChargingCSV": [_sym("IDynamicChargingCSV", 11, "a/I.java", 1, 50)],
    })
    seeds = find_seeds(s, [
        SearchTerm(term="batchStart", kind="camelcase"),
        SearchTerm(term="MAX", kind="shouty"),
        SearchTerm(term="IDynamicChargingCSV", kind="proper_noun"),
    ])
    by_target = {c.target: c.kind for c in seeds}
    assert by_target["a.B.batchStart"] == "METHOD"
    assert by_target["a.B.MAX"] == "FIELD"
    assert by_target["IDynamicChargingCSV"] == "INTERFACE"
    # 每个原词都被查;前 3 个是原词,query_expand 对 4 词复合名 IDynamicChargingCSV
    # 追加前缀查询串(IDynamicCharging / IDynamic)。
    assert s.queries[:3] == ["batchStart", "MAX", "IDynamicChargingCSV"]
    assert "IDynamicCharging" in s.queries


def test_compound_term_decomposition_finds_real_class():
    """02 拼的 DynamicChargingBatch 撞不上, 但 query_expand 拆出的 DynamicCharging
    能撞上真类;强度对【原词】算 = 0.6 模糊, 不虚高(手测 2026-06-01 实证的真实失败链)。"""
    from contextos.code_intel.code_search.seeds import find_seeds
    real = _sym("DynamicChargingSVImpl", 5, "order/.../DynamicChargingSVImpl.java", 29, 29,
                container="com.example.order.soa.biz.intf.orderservice.impl")
    # workspaceSymbol 对复合名返回空, 对拆出的核心前缀返回真类
    s = FakeSearcher({"DynamicCharging": [real]})  # "DynamicChargingBatch" 不在表 -> 空
    seeds = find_seeds(s, [SearchTerm(term="DynamicChargingBatch", kind="camelcase")])
    assert len(seeds) == 1
    assert seeds[0].target.endswith("DynamicChargingSVImpl")
    # 经拆解前缀找到 -> 对原词 DynamicChargingBatch 算 = 模糊 0.6(关键:不因前缀命中虚高成 1.0)
    assert seeds[0].signals["name_match_strength"] == 0.6
    assert "DynamicCharging" in s.queries  # 确实查了拆出的前缀


def test_unknown_kind_maps_to_other():
    from contextos.code_intel.code_search.seeds import find_seeds
    s = FakeSearcher({"x": [_sym("x", 13, "a/B.java", 1, 1)]})  # 13 = Variable
    seeds = find_seeds(s, [SearchTerm(term="x", kind="other")])
    assert seeds[0].kind == "OTHER"


def test_constructor_constant_enum_kind_mapping():
    """Constructor(9)->METHOD / Constant(14)->FIELD / Enum(10)->CLASS(code review I-3)。"""
    from contextos.code_intel.code_search.seeds import find_seeds
    s = FakeSearcher({
        "Foo": [_sym("Foo", 9, "a/Foo.java", 1, 1, container="a.Foo")],            # Constructor
        "TAX_RATE": [_sym("TAX_RATE", 14, "a/Foo.java", 2, 2, container="a.Foo")],  # Constant
        "Color": [_sym("Color", 10, "a/Color.java", 1, 9, container="a")],         # Enum
    })
    seeds = find_seeds(s, [
        SearchTerm(term="Foo", kind="proper_noun"),
        SearchTerm(term="TAX_RATE", kind="shouty"),
        SearchTerm(term="Color", kind="proper_noun"),
    ])
    by_target = {c.target: c.kind for c in seeds}
    assert by_target["a.Foo.Foo"] == "METHOD"
    assert by_target["a.Foo.TAX_RATE"] == "FIELD"
    assert by_target["a.Color"] == "CLASS"


def test_empty_name_symbol_skipped():
    """无 name 的畸形符号被跳过,不以 "" 撞 dedup key(code review I-1)。"""
    from contextos.code_intel.code_search.seeds import find_seeds
    s = FakeSearcher({"x": [_sym("", 5, "a/B.java", 1, 1), _sym("RealClass", 5, "a/R.java", 1, 9)]})
    seeds = find_seeds(s, [SearchTerm(term="x", kind="other")])
    assert [c.target for c in seeds] == ["RealClass"]


def test_dedup_keeps_higher_strength():
    """同 target 被两个 term 命中(精确 + 模糊),保留精确(1.0)。"""
    from contextos.code_intel.code_search.seeds import find_seeds
    sym = _sym("Foo", 5, "a/Foo.java", 1, 2)
    s = FakeSearcher({"Foo": [sym], "Fo": [sym]})
    seeds = find_seeds(s, [SearchTerm(term="Fo", kind="other"),
                           SearchTerm(term="Foo", kind="camelcase")])
    assert len(seeds) == 1
    assert seeds[0].signals["name_match_strength"] == 1.0


def test_empty_terms_and_no_hits():
    from contextos.code_intel.code_search.seeds import find_seeds
    assert find_seeds(FakeSearcher({}), []) == []
    assert find_seeds(FakeSearcher({}), [SearchTerm(term="Nope", kind="other")]) == []
