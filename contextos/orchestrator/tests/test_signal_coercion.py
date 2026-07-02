# contextos/orchestrator/tests/test_signal_coercion.py
"""08 score_bridge / RAG 投影的 fail-safe coercion 守护(Plan 08 WF1 实证探针驱动)。

设计思路
--------
score_bridge 在逐候选 loop 里跑(pipeline._pool_for_rerank / corroboration.corroborate_one)。
若某条候选的 `signals` dict 里某个值是坏类型(None / 非数值 str / list / NaN / inf),裸
`float(...)` / `int(...)` / `Q_BIND.get(unhashable, ...)` 会抛异常 -> 崩掉整轮编排,违反
08 design §5.1「provider 失败当 miss,不阻塞」的 fail-safe 红线(一条坏候选不该带走全部召回)。

WF1 的两个对抗实证探针(/tmp/probe_math_08*.py、probe_id_08*.py)证明:这些坏类型输入在
**当前 U0 typed-signal 契约下不可达**(各 provider 用强类型 pydantic / SQLAlchemy 模型校验后
才 dump 进开放 signals dict)。但 orchestrator 是融合层 + 公开 API(Task 12 导出)+ Plan 10
MCP host 输入路径的下游,故做 defense-in-depth:provider_io._safe_float / _safe_int 把坏类型
coerce 成 default(0),corroboration / rag_projection 的取分路径统一走它。

评分标准(本文件断言什么)
------------------------
1. _safe_float / _safe_int 对每类坏输入返回 default、对合法输入返回正确值(NaN/inf 当垃圾 -> 0,
   不奖励满分:否则 _clamp01(nan) 在某些 min/max 取参顺序下会漏成 1.0)。
2. score_bridge 四条桥路径喂坏 signal 值都返回 [0,1] float、绝不抛异常。
3. _score_config 的 bind_strategy 为 unhashable(list)时返回 0.3 兜底,不 raise TypeError。
4. build_rag_projection 的 rerank_score 为非 coercible str 时不崩,该文档降级(score 0.0)。
5. 端到端 corroborate_one 喂混入坏值的 signals 仍出 [0,1] 分、不崩(fail-safe 集成断言)。

注:合法输入的取分语义已由 test_corroboration_primitives.py / test_corroborate.py 守护,
本文件**只**守 coercion 的 fail-safe 边界,不重复合法路径断言。
"""
import math
from types import SimpleNamespace

from contextos.impact_map.schema import ImpactMap
from contextos.orchestrator.assemble import (
    _build_config_binding,
    _build_sql_lineage,
    assemble_impact_map,
    to_evidence_item,
)
from contextos.orchestrator.corroboration import (
    CorroboratedCandidate,
    corroborate_one,
    score_bridge,
)
from contextos.orchestrator.provider_io import ProviderCandidate, _safe_float, _safe_int
from contextos.orchestrator.rag_projection import RagProjection, build_rag_projection
from contextos.profile.schema import CorroborationConfig

CFG = CorroborationConfig()
NO_RAG = RagProjection([])


def _cc(kind, signals_by_worker, *, score=0.5, target="t"):
    return CorroboratedCandidate(
        target=target, kind=kind, score_overall=score, confidence_tier="MEDIUM",
        bridge_scores={}, consensus_count=1, hit_workers=list(signals_by_worker),
        signals_by_worker=signals_by_worker, rag_score=0.0)


def _bd(**kw):
    base = dict(requirement_id="r1", raw_text="t", source_kind="text", assessment="ok",
                confidence=1.0, business_intent="bi", actions=["modify"],
                matched_capabilities=[], open_questions=[])
    base.update(kw)
    return SimpleNamespace(**base)


def test_safe_float_bad_inputs_to_default():
    for bad in (None, "x", [1], {}, object(), float("nan"), float("inf"), float("-inf")):
        assert _safe_float(bad) == 0.0
    assert _safe_float(None, default=0.3) == 0.3       # 自定义 default


def test_safe_float_valid_inputs():
    assert _safe_float(0.5) == 0.5
    assert _safe_float("0.5") == 0.5                   # 数值 str 仍 coerce
    assert _safe_float(True) == 1.0
    assert _safe_float(2) == 2.0


def test_safe_int_bad_inputs_to_default():
    for bad in ("x", None, [1, 2], {}, float("nan"), float("inf"), "3.5"):
        assert _safe_int(bad) == 0
    assert _safe_int("x", default=7) == 7


def test_safe_int_valid_inputs():
    assert _safe_int(3) == 3
    assert _safe_int("3") == 3
    assert _safe_int(2.9) == 2                         # 截断,与原 int(...) 一致
    assert _safe_int(True) == 1


def test_score_bridge_code_search_bad_values_no_crash():
    for bad in (None, "x", [1], float("nan"), float("inf")):
        s = score_bridge("code_search", {"name_match_strength": bad})
        assert s == 0.0                                # 坏类型 -> 0,不崩、不漏成满分


def test_score_bridge_llm_rerank_bad_values_no_crash():
    for bad in (None, "x", [1], float("nan")):
        s = score_bridge("llm_rerank", {"vote_score": bad})
        assert s == 0.0


def test_score_bridge_db_bad_evidence_count_no_crash():
    # evidence_count 坏类型 -> 当 0(<2),literal 基分 1.0 不变;不抛 int() 异常
    for bad in ("x", float("nan"), float("inf"), [1, 2], None):
        s = score_bridge("db_lineage_bridge",
                         {"recovery_mode": "literal", "evidence_count": bad})
        assert s == 1.0


def test_score_bridge_config_unhashable_bind_strategy_no_crash():
    # bind_strategy 为 list(unhashable)-> 0.3 兜底,不抛 TypeError: unhashable type
    s = score_bridge("config_dimension_bridge", {"bind_strategy": ["x"]})
    assert s == 0.3


def test_score_bridge_results_always_in_unit_interval():
    for worker, sig in [
        ("code_search", {"name_match_strength": 99}),
        ("code_search", {"name_match_strength": -5}),
        ("llm_rerank", {"vote_score": 50}),
        ("db_lineage_bridge", {"recovery_mode": "literal", "evidence_count": 3, "branch_detected": True}),
        ("config_dimension_bridge", {"table": "T", "resolved_owner": "X"}),
    ]:
        s = score_bridge(worker, sig)
        assert 0.0 <= s <= 1.0 and math.isfinite(s)


def test_build_rag_projection_non_coercible_rerank_score_no_crash():
    # 非 coercible rerank_score str -> 该文档降级(_safe_float -> 0.0),不抛 ValueError
    p = build_rag_projection([
        ProviderCandidate(target="docs/x.md", kind="BUSINESS_DOC",
                          signals={"snippet": "TBL_X here", "rerank_score": "high"})])
    assert p.score_for("TBL_X") == 0.0                 # 命中但分降级为 0


def test_corroborate_one_with_garbage_signal_is_fail_safe():
    # 端到端:某桥 signal 混入坏值,corroborate_one 仍出 [0,1] 分、不崩
    cc = corroborate_one(
        target="com.x.Foo", kind="METHOD",
        signals_by_worker={"code_search": {"name_match_strength": None},
                           "llm_rerank": {"vote_score": "garbage"}},
        rag_proj=NO_RAG, cfg=CFG)
    assert 0.0 <= cc.score_overall <= 1.0
    assert cc.confidence_tier in ("HIGH", "MEDIUM", "LOW")
    assert cc.bridge_scores["code_search"] == 0.0
    assert cc.bridge_scores["llm_rerank"] == 0.0


# --- assemble fail-safe symmetry (WF2 assemble-probe driven) -------------------
# assemble 读同一份 open signals dict,须与 corroboration.score_bridge 一样对坏类型 / 越界值
# 容错(否则 corroborate 放行的坏候选会在 assemble 崩掉整轮,defense-in-depth asymmetry)。
# assemble 的契约 = 永不抛 ValidationError(总产出合法 01 ImpactMap)。05/06 store 列是无约束
# String/Integer,启发式抽取漂移到非枚举值是真实(虽当前未发生)风险。


def test_assemble_sql_bad_evidence_count_floors_to_one():
    sl = _build_sql_lineage({"dst": {"table": "T"}, "recovery_mode": "literal",
                             "evidence_count": "abc"})
    assert sl.evidence_count == 1                       # 坏类型 -> 0 -> G1 floor 1,不抛 ValueError


def test_assemble_sql_unknown_recovery_mode_falls_back():
    sl = _build_sql_lineage({"dst": {"table": "T"}, "recovery_mode": "GARBAGE_MODE"})
    assert sl.recovery_mode == "literal"                # 未知 -> literal,不抛 Literal ValidationError


def test_assemble_sql_partial_and_extra_key_src_no_crash():
    # partial src(缺 db/owner)+ extra key 都不该崩(与 dst 对称稳健建)
    sl = _build_sql_lineage({"dst": {"table": "T"}, "src": {"table": "S", "junk": 1}})
    assert sl.src is not None and sl.src.table == "S" and sl.src.db == ""
    sl2 = _build_sql_lineage({"dst": {"table": "T"}, "src": {"no_table": 1}})
    assert sl2.src is None                              # 无 table -> None


def test_assemble_config_unknown_entity_and_bind_type_fall_back():
    cb = _build_config_binding("CONFIG_KEY",
        {"entity_type": "GARBAGE_ENTITY", "bind_type": "GARBAGE_BIND",
         "bind_strategy": "exact_match"})
    assert cb.entity_type == "file_key"                 # 未知 -> file_key
    assert cb.bind_type == "domain"                     # 未知 -> domain


def test_assemble_confidence_clamped():
    over = to_evidence_item(_cc("METHOD", {"code_search": {"name_match_strength": 1.0}}, score=1.5),
                            "ev0000", ["modify"])
    assert over.confidence == 1.0                       # 越界 1.5 -> clamp 1.0,不抛 le=1.0 ValidationError
    under = to_evidence_item(_cc("METHOD", {"code_search": {}}, score=-0.3), "ev0001", ["modify"])
    assert under.confidence == 0.0


def test_assemble_impact_map_never_raises_on_garbage_signals():
    # 端到端:混入坏 signal 的三维候选,assemble_impact_map 仍产出通过 3 个 validator 的合法 ImpactMap
    corrobs = [
        _cc("SQL_TABLE", {"db_lineage_bridge": {"dst": {"table": "T"}, "recovery_mode": "??",
                                                "evidence_count": "x", "src": {"junk": 1}}},
            target="X.T"),
        _cc("CONFIG_KEY", {"config_dimension_bridge": {"entity_type": "??", "bind_type": "??",
                                                       "bind_strategy": "hierarchical_match"}},
            target="k.k", score=1.7),
        _cc("METHOD", {"code_search": {"name_match_strength": 1.0}}, target="com.x.Foo"),
    ]
    im = assemble_impact_map(_bd(), corrobs)
    assert isinstance(im, ImpactMap)                    # 通过全部 3 个 model_validator
    ImpactMap.model_validate(im.model_dump())           # 二次确认序列化往返合法
    assert len(im.evidence_items) == 3
    assert all(0.0 <= e.confidence <= 1.0 for e in im.evidence_items)


def test_assemble_unknown_kind_normalized_to_other_not_crash():
    # eligible_bridges 对未知/空 kind 兜底 {llm_rerank}(corroboration 不空分母), 但 01 EvidenceItem.kind
    # 是闭 Literal -> assemble 须归一未知/空 kind 到 OTHER(原值留 metadata.raw_kind), 否则 ValidationError 崩整轮。
    # ProviderCandidate.kind 是开放 str(08 §2 扩展性), 未来 provider / Plan 10 MCP host 可能吐非枚举 kind。
    for bad in ("FUTURE_KIND", "", "garbage-kind"):
        cc = _cc(bad, {"code_search": {"name_match_strength": 1.0}}, target="com.x.Foo")
        im = assemble_impact_map(_bd(), [cc])
        ev = im.evidence_items[0]
        assert ev.kind == "OTHER"                       # 归一, 不崩
        assert ev.metadata["raw_kind"] == bad           # 原值留痕供 audit
        ImpactMap.model_validate(im.model_dump())       # 仍过 3 个 validator
    # 合法 kind 不加 raw_kind 噪音
    ev = assemble_impact_map(_bd(), [_cc("CLASS", {"code_search": {"name_match_strength": 1.0}})]).evidence_items[0]
    assert ev.kind == "CLASS" and "raw_kind" not in ev.metadata
