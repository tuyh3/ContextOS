# contextos/orchestrator/tests/test_corroborate.py
from contextos.orchestrator.corroboration import corroborate, corroborate_one
from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.orchestrator.rag_projection import RagProjection
from contextos.profile.schema import CorroborationConfig

CFG = CorroborationConfig()
NO_RAG = RagProjection([])


def test_corroborate_one_method_high():
    # 方法维:code 1.0 + llm 0.85 -> 0.714*1.0 + 0.286*0.85 + 0.1(共识) clamp
    cc = corroborate_one(
        target="com.x.FooSVImpl#bar", kind="METHOD",
        signals_by_worker={"code_search": {"name_match_strength": 1.0},
                           "llm_rerank": {"vote_score": 0.85}},
        rag_proj=NO_RAG, cfg=CFG)
    assert cc.consensus_count == 2
    assert cc.confidence_tier == "HIGH"
    assert cc.score_overall > 0.75
    assert cc.bridge_scores == {"code_search": 1.0, "llm_rerank": 0.85}


def test_corroborate_one_method_missing_bridge_low():
    # 只有 code 一座 + 低分,llm 缺(eligible-but-missed -> 0 留分母)
    cc = corroborate_one(
        target="com.x.Foo", kind="CLASS",
        signals_by_worker={"code_search": {"name_match_strength": 0.6}},
        rag_proj=NO_RAG, cfg=CFG)
    assert cc.bridge_scores == {"code_search": 0.6, "llm_rerank": 0.0}
    assert cc.consensus_count == 1                  # 仅 code 0.6 >= 0.6
    assert cc.confidence_tier == "MEDIUM"


def test_corroborate_one_sql_rag_projection():
    # SQL 维:db 命中 + RAG 投影命中表名(不走 target 对齐 G5)+ llm 缺
    rag = RagProjection([("see table pm_offer in spec", 0.9)])
    cc = corroborate_one(
        target="CCRM3.UPC.PM_OFFER", kind="SQL_TABLE",
        signals_by_worker={"db_lineage_bridge": {"recovery_mode": "literal", "evidence_count": 3}},
        rag_proj=rag, cfg=CFG)
    # db score=1.0(literal 1.0 +0.1 ev>=2 clamp 1.0), rag=0.9, llm missed=0
    assert cc.bridge_scores["db_lineage_bridge"] == 1.0
    assert cc.bridge_scores["rag"] == 0.9
    assert cc.bridge_scores["llm_rerank"] == 0.0
    assert cc.rag_score == 0.9
    assert "rag" in cc.hit_workers
    # db 0.44*1.0 + rag 0.33*0.9 + llm 0.22*0 = 0.737; 共识 2(db+rag) -> +0.1 bonus -> 0.837 HIGH
    assert cc.confidence_tier == "HIGH"


def test_corroborate_one_method_no_rag():
    # method 维 eligible 不含 rag,即便 RAG 命中也不计
    rag = RagProjection([("FooSVImpl mentioned", 0.9)])
    cc = corroborate_one(
        target="com.x.FooSVImpl", kind="CLASS",
        signals_by_worker={"code_search": {"name_match_strength": 1.0}},
        rag_proj=rag, cfg=CFG)
    assert "rag" not in cc.bridge_scores
    assert cc.rag_score == 0.0


def test_corroborate_aligns_across_bridges_by_target():
    code = ProviderResult(worker_name="code_search", score=1.0, candidates=[
        ProviderCandidate(target="com.x.Foo", kind="CLASS", signals={"name_match_strength": 1.0})])
    llm = ProviderResult(worker_name="llm_rerank", score=0.8, candidates=[
        ProviderCandidate(target="com.x.Foo", kind="CLASS", signals={"vote_score": 0.8, "vote": "support", "status": "ok"})])
    out = corroborate(cheap_results={"code_search": code}, rerank_result=llm,
                      rag_proj=NO_RAG, cfg=CFG)
    assert len(out) == 1
    assert out[0].target == "com.x.Foo"
    assert sorted(out[0].hit_workers) == ["code_search", "llm_rerank"]
    assert out[0].confidence_tier == "HIGH"


def test_corroborate_skips_rag_candidates_as_targets():
    # RAG 的 BUSINESS_DOC 候选不进逐 target 对齐(投影特例)
    rag_res = ProviderResult(worker_name="rag", score=0.9, candidates=[
        ProviderCandidate(target="docs/a.md", kind="BUSINESS_DOC",
                          signals={"snippet": "pm_offer table", "rerank_score": 0.9})])
    code = ProviderResult(worker_name="code_search", score=1.0, candidates=[
        ProviderCandidate(target="com.x.Foo", kind="CLASS", signals={"name_match_strength": 1.0})])
    out = corroborate(cheap_results={"code_search": code, "rag": rag_res},
                      rerank_result=ProviderResult(worker_name="llm_rerank", score=0.0),
                      rag_proj=NO_RAG, cfg=CFG)
    targets = {c.target for c in out}
    assert "docs/a.md" not in targets          # 文档不当 impact 候选
    assert "com.x.Foo" in targets


def test_corroborate_same_target_two_dimensions_kept_separate():
    # review HIGH 1:同物理表被 05 SQL_TABLE + 06 CONFIG_TABLE 命中(target 同 kind 不同)-> 两候选都不丢
    sql = ProviderResult(worker_name="db_lineage_bridge", score=1.0, candidates=[
        ProviderCandidate(target="UPC.PM_OFFER", kind="SQL_TABLE",
            signals={"recovery_mode": "literal", "evidence_count": 2})])
    cfg = ProviderResult(worker_name="config_dimension_bridge", score=0.8, candidates=[
        ProviderCandidate(target="UPC.PM_OFFER", kind="CONFIG_TABLE",
            signals={"table": "PM_OFFER", "resolved_owner": "UPC"})])
    out = corroborate(cheap_results={"db_lineage_bridge": sql, "config_dimension_bridge": cfg},
                      rerank_result=ProviderResult(worker_name="llm_rerank", score=0.0),
                      rag_proj=NO_RAG, cfg=CFG)
    keys = {(c.kind, c.target) for c in out}
    assert ("SQL_TABLE", "UPC.PM_OFFER") in keys
    assert ("CONFIG_TABLE", "UPC.PM_OFFER") in keys
    assert len(out) == 2
