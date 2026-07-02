# contextos/orchestrator/tests/test_corroboration_primitives.py
import pytest

from contextos.orchestrator.corroboration import (
    eligible_bridges,
    renormalize,
    score_bridge,
    bucket,
    base_weights,
)
from contextos.profile.schema import CorroborationConfig

CFG = CorroborationConfig()


def test_eligible_method():
    assert eligible_bridges("METHOD") == frozenset({"code_search", "llm_rerank"})
    assert eligible_bridges("API_ENTRY") == frozenset({"code_search", "llm_rerank"})


def test_eligible_sql_config_other():
    assert eligible_bridges("SQL_TABLE") == frozenset({"db_lineage_bridge", "rag", "llm_rerank"})
    assert eligible_bridges("CONFIG_KEY") == frozenset({"config_dimension_bridge", "rag", "llm_rerank"})
    # OTHER / 未知 / v2 占位 -> 兜底单 llm(绝不空分母)
    assert eligible_bridges("OTHER") == frozenset({"llm_rerank"})
    assert eligible_bridges("MENU") == frozenset({"llm_rerank"})


def test_renormalize_method_weights():
    w = renormalize(frozenset({"code_search", "llm_rerank"}), base_weights(CFG))
    # 0.25/0.35=0.714, 0.10/0.35=0.286(design §3.1: code 0.71 / llm 0.29)
    assert round(w["code_search"], 2) == 0.71
    assert round(w["llm_rerank"], 2) == 0.29
    assert round(sum(w.values()), 6) == 1.0


def test_renormalize_sql_weights():
    w = renormalize(frozenset({"db_lineage_bridge", "rag", "llm_rerank"}), base_weights(CFG))
    assert round(w["db_lineage_bridge"], 2) == 0.44
    assert round(w["rag"], 2) == 0.33
    assert round(w["llm_rerank"], 2) == 0.22


def test_score_bridge_code_and_llm():
    assert score_bridge("code_search", {"name_match_strength": 1.0}) == 1.0
    assert score_bridge("code_search", {}) == 0.0                 # 缺字段 -> 0
    assert score_bridge("llm_rerank", {"vote_score": 0.85}) == 0.85


def test_score_bridge_db_recovery_formula():
    # literal(1.0) - 0.2*branch + 0.1*[ev>=2]
    assert score_bridge("db_lineage_bridge", {"recovery_mode": "literal", "branch_detected": False, "evidence_count": 1}) == 1.0
    assert score_bridge("db_lineage_bridge", {"recovery_mode": "sql_file", "branch_detected": True, "evidence_count": 3}) == 0.9
    assert score_bridge("db_lineage_bridge", {"recovery_mode": "string_builder", "branch_detected": False, "evidence_count": 1}) == 0.4
    # 未知 recovery_mode -> 0.4 fallback
    assert score_bridge("db_lineage_bridge", {"recovery_mode": "???", "evidence_count": 1}) == 0.4


def test_score_bridge_config_table_and_key():
    # CONFIG_TABLE: owner-resolved 0.6 / 裸名 0.4
    assert score_bridge("config_dimension_bridge", {"table": "T", "resolved_owner": "APP1"}) == 0.6
    assert score_bridge("config_dimension_bridge", {"table": "T", "resolved_owner": ""}) == 0.4
    # CONFIG_KEY: Q_bind[bind_strategy]; exact_match=1.0, 缺 strategy -> 0.3
    assert score_bridge("config_dimension_bridge", {"entity_key": "k", "bind_strategy": "exact_match"}) == 1.0
    assert score_bridge("config_dimension_bridge", {"entity_key": "k"}) == 0.3


def test_bucket_tiers():
    # HIGH 需 score>=0.75 AND 共识>=2
    assert bucket(0.9, 2, CFG) == "HIGH"
    assert bucket(0.9, 1, CFG) == "MEDIUM"     # 高分但无共识 -> 共识门封顶 MEDIUM
    assert bucket(0.5, 1, CFG) == "MEDIUM"
    assert bucket(0.5, 2, CFG) == "MEDIUM"     # 0.4<=score<0.75
    assert bucket(0.3, 1, CFG) == "MEDIUM"     # 仅 1 桥 >=0.6 -> MEDIUM(§3.2)
    assert bucket(0.3, 0, CFG) == "LOW"
    assert bucket(0.0, 0, CFG) == "LOW"
