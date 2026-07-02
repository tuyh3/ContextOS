"""Task C3: config_dimension_bridge provider(search_config -> ProviderResult)。

设计思路:
- design §12/§13 + spec §1:v1 输出 direct_bindings(不出 BFS transitive 调用链);
  §13 corroboration 子分骨架(business_relevance/rag_corroboration v1 占位,09 校准)。
- 复用 contextos.orchestrator.provider_io.ProviderResult/ProviderCandidate(08 §2 SSOT)。

评分标准(test 守护):
- 命中 config key -> worker_name=config_dimension_bridge / score>0 / 候选 kind=CONFIG_KEY;
  候选 signals 带最佳 binding 的 bind_target,且 **不含 callers**(direct_bindings 不含 transitive)。
- 无命中 -> ProviderResult.miss(score=0 + miss_reason 非空)。

自动脚本逻辑:种 1 file source + 1 entity(offer.switch.enable)+ 1 exact_match binding;
search_config 用子串大小写不敏感匹配 candidate_config_keys 命中 entity_key。
"""
from types import SimpleNamespace

from sqlalchemy import create_engine, insert

from contextos.config_dim.provider import search_config
from contextos.config_dim.schema import (
    config_bindings,
    config_entities,
    config_sources,
    metadata,
)


class _BD:  # 最小 RequirementBreakdown 替身: 候选 list items 暴露 .term(真契约是 .term-bearing, 非裸 str)
    def __init__(self, keys, tables):
        self.candidate_config_keys = [SimpleNamespace(term=k) for k in keys]
        self.candidate_table_terms = [SimpleNamespace(term=t) for t in tables]


def _seed(eng):
    with eng.begin() as c:
        c.execute(insert(config_sources).values(source_id="s1", source_type="file", file_path="app.yml"))
        c.execute(insert(config_entities).values(entity_id="e1", source_id="s1",
                                                 entity_key="offer.switch.enable", entity_type="file_key"))
        c.execute(insert(config_bindings).values(binding_id="b1", entity_id="e1", bind_type="java_class",
                                                 bind_target="com.x.OfferConfig", bind_strategy="exact_match",
                                                 confidence="high"))


def test_provider_matches_config_key_returns_direct_binding():
    eng = create_engine("sqlite:///:memory:"); metadata.create_all(eng); _seed(eng)
    res = search_config(_BD(keys=["offer.switch"], tables=[]), eng)
    assert res.worker_name == "config_dimension_bridge"
    assert res.score > 0
    c0 = res.candidates[0]
    assert c0.kind == "CONFIG_KEY" and c0.target == "offer.switch.enable"
    assert c0.signals["bind_target"] == "com.x.OfferConfig"
    # direct_bindings, 不含 transitive 调用链
    assert "callers" not in c0.signals


def test_provider_miss_returns_miss():
    eng = create_engine("sqlite:///:memory:"); metadata.create_all(eng)
    res = search_config(_BD(keys=["nope"], tables=[]), eng)
    assert res.miss_reason is not None and res.score == 0
