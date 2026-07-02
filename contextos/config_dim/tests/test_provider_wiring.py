"""Task W3: provider table 维候选 + owner 取 config_sources.owner(wiring 守护)。

设计思路:
- design §12/§13 + Plan 06c W3:provider 除 config_key 维外,补 table 维候选 ——
  candidate_table_terms 子串命中 config_sources(source_type=db_table)的 table_name。
- table 维 owner **直接取 config_sources.owner**(path B 在 pipeline.py:174 已写),
  **不** JOIN / 全表扫 owner_resolution(那是 edge-keyed overlay 给 lineage 用,Plan 10 延后)。

评分标准(test 守护):
- owner 空 -> 候选 target = 裸表名(table 维命中,score>0)。
- owner 非空 -> 候选 target = "OWNER.TABLE"(owner 来自 config_sources)。
- 负样本(HIGH 2):别 edge 的 owner_resolution 行绝不富化无关 config 表(owner 仍空)。

自动脚本逻辑:种 config_sources(db_table)行 + 可选 owner_resolution 干扰行;
search_config 用 candidate_table_terms 子串大小写不敏感匹配 table_name。
"""
from types import SimpleNamespace

from sqlalchemy import create_engine, insert
from contextos.config_dim.schema import metadata, config_sources, owner_resolution
from contextos.config_dim.provider import search_config


class _BD:  # 真 RequirementBreakdown 候选是 .term-bearing pydantic(非裸 str); stub 的 list items 暴露 .term
    def __init__(self, keys, tables):
        self.candidate_config_keys = [SimpleNamespace(term=k) for k in keys]
        self.candidate_table_terms = [SimpleNamespace(term=t) for t in tables]


def test_provider_table_dim_bare_table_when_owner_empty():
    eng = create_engine("sqlite:///:memory:"); metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(insert(config_sources).values(source_id="s1", source_type="db_table",
                                                owner="", table_name="PM_OFFER_CHA", db_name="CTEST"))
    res = search_config(_BD(keys=[], tables=["PM_OFFER"]), eng)
    assert res.score > 0
    cand = [x for x in res.candidates if x.kind == "CONFIG_TABLE" and "PM_OFFER" in x.target]
    assert cand and cand[0].target.endswith("PM_OFFER_CHA")  # owner 空 -> 裸表名(table 维候选命中)


def test_provider_table_dim_uses_config_source_owner():
    """table 维候选的 owner 来自 config_sources.owner(path B 在 pipeline.py:174 已设),
    不靠全表扫 owner_resolution。"""
    eng = create_engine("sqlite:///:memory:"); metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(insert(config_sources).values(source_id="s1", source_type="db_table",
                                                owner="UPC", table_name="PM_OFFER_CHA", db_name="CTEST"))
    res = search_config(_BD(keys=[], tables=["PM_OFFER"]), eng)
    cand = [x for x in res.candidates if "PM_OFFER_CHA" in x.target][0]
    assert cand.target == "UPC.PM_OFFER_CHA"  # owner 来自 config_sources, 非全表扫


def test_provider_unrelated_owner_resolution_does_not_bleed():
    """负样本(HIGH 2): 别 edge 的 owner_resolution 行绝不能富化无关 config 表(owner='')。
    旧蓝本 `_resolved_owner` 全表扫 owner_resolution -> 会错把别表 owner 安到这张表。
    owner_resolution overlay 是 edge-keyed 给 lineage(trace_config_impact, Plan 10), 不在
    config-table-owner 路径。"""
    eng = create_engine("sqlite:///:memory:"); metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(insert(config_sources).values(source_id="s1", source_type="db_table",
                                                owner="", table_name="COMMON_T", db_name="CTEST"))
        c.execute(insert(owner_resolution).values(edge_id="E_other", module="m", datasource_key="d",
                                                  resolved_src_owner="UPC"))  # 无关边的 overlay
    res = search_config(_BD(keys=[], tables=["COMMON_T"]), eng)
    cand = [x for x in res.candidates if "COMMON_T" in x.target][0]
    assert cand.target == "COMMON_T"  # owner 仍空, 不被别表 overlay 污染
    assert cand.signals.get("resolved_owner", "") == ""


def test_provider_table_dim_real_breakdown_contract():
    """HIGH(W3): 用**真** RequirementBreakdown + CandidateTableTerm(.term-bearing pydantic)验跨模块契约。
    复现 reviewer adversarial: 旧 str(t) 对真 pydantic 取 repr 永不匹配 -> score=0.0; 修后 t.term 命中。
    中性合成 fixture。"""
    from contextos.requirement.schema import RequirementBreakdown, CandidateTableTerm
    eng = create_engine("sqlite:///:memory:"); metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(insert(config_sources).values(source_id="s1", source_type="db_table",
                                                owner="APP1", table_name="APP_PARAM", db_name="TESTDB"))
    bd = RequirementBreakdown(
        requirement_id="r1", raw_text="x", source_kind="text",
        candidate_table_terms=[CandidateTableTerm(term="APP_PAR", kind="entity", source="llm")])
    res = search_config(bd, eng)
    assert res.score > 0  # 修前真 breakdown -> score=0.0(reviewer adversarial); 修后 .term 命中
    cand = [x for x in res.candidates if "APP_PARAM" in x.target][0]
    assert cand.target == "APP1.APP_PARAM"  # owner 取 config_sources.owner
    # kind 契约(01 §3.1 + design §0 banner kind SSOT): 配置维 table 候选 = CONFIG_TABLE,
    # 绝不吐 05 血缘维的 SQL_TABLE。修前 provider 误吐 SQL_TABLE -> 08 编排会误归血缘维。
    from contextos.impact_map.enums import KIND_CONFIG_DIMENSION, KIND_SQL_DIMENSION
    assert cand.kind == "CONFIG_TABLE"
    assert cand.kind in KIND_CONFIG_DIMENSION
    assert cand.kind not in KIND_SQL_DIMENSION
