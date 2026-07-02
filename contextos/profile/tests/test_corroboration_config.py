"""Plan 08 Task 1: CorroborationConfig namespace + 向后兼容默认。"""
from __future__ import annotations

import pytest

from contextos.profile.schema import CorroborationConfig, Profile


@pytest.fixture
def minimal_profile_dict() -> dict:
    """最小合法 Profile dict(无 [corroboration] 段)。

    镜像 test_schema.py 的 _minimal_dict():9 个必填 namespace,
    用中性合成值(见 [[feedback_offline_test_neutral_fixtures]])。
    """
    return {
        "llm": {"provider": "claude", "api_key_env": "ANTHROPIC_API_KEY"},
        "embedding": {"model": "BAAI/bge-m3", "device": "cpu"},
        "reranker": {"enabled": True, "model": "BAAI/bge-reranker-v2-m3",
                     "top_k_input": 50, "top_k_output": 10},
        "query_expansion": {"enabled": True, "translation_provider": "main_llm",
                            "fallback_provider": "local_qwen_2_5_7b"},
        "storage": {"data_dir": "/tmp/contextos-data"},
        "ingestion": {"default_cleanup": "full", "chunk_strategy": "h2_h3",
                      "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "/opt/jdtls/server",
                          "lombok_path": "/opt/jdtls/lombok.jar",
                          "java_home": "/opt/jre21"},
        "oracle": {"tns_admin": "/etc/tns",
                   "allowed_instances": ["TEST_DB1"]},
        "projects": [{"name": "demoproj", "path": "/data/demoproj",
                      "language": "java", "build_system": "gradle"}],
    }


def test_corroboration_defaults():
    c = CorroborationConfig()
    # 基权初值(design §3.1, 和 = 1.0; eligible-set 重归一化在 corroboration 内)
    assert c.w_code_search == 0.25
    assert c.w_db_lineage == 0.20
    assert c.w_config_dimension == 0.15
    assert c.w_rag == 0.15
    assert c.w_dict == 0.15
    assert c.w_llm_rerank == 0.10
    assert c.alpha_consensus == 0.10
    assert c.high_threshold == 0.75
    assert c.medium_threshold == 0.4
    assert c.consensus_score == 0.6
    assert c.consensus_min_bridges == 2


def test_corroboration_strict_extra_forbidden():
    import pytest
    with pytest.raises(Exception):
        CorroborationConfig.model_validate({"w_code_search": 0.3, "bogus": 1})


def test_profile_has_corroboration_default(minimal_profile_dict):
    # 既有 profile(无 [corroboration] 段)仍可加载 -> default_factory 不破坏向后兼容
    p = Profile.model_validate(minimal_profile_dict)
    assert isinstance(p.corroboration, CorroborationConfig)
    assert p.corroboration.w_code_search == 0.25


def test_corroboration_rejects_negative_weight_and_bad_bounds():
    # 负/坏权重静默扭曲置信度(负权重 -> renormalize 出负有效权重; 虽 score_overall 终被 clamp,
    # 但相对排序失真)-> profile 加载期就拒, 不留到运行时。阈值 [0,1]; consensus 桥数 >=1。
    for bad in (
        {"w_code_search": -0.5},      # 负权重
        {"w_rag": -0.01},
        {"alpha_consensus": -0.1},    # 负 bonus
        {"high_threshold": 1.5},      # 阈值越界
        {"medium_threshold": -0.1},
        {"consensus_score": 2.0},
        {"consensus_min_bridges": 0}, # 共识桥数须 >=1
    ):
        with pytest.raises(Exception):
            CorroborationConfig(**bad)
    # 合法边界值(0 权重 / 0.0 / 1.0 阈值 / 1 桥)仍接受
    ok = CorroborationConfig(w_dict=0.0, high_threshold=1.0, medium_threshold=0.0,
                             consensus_score=0.0, consensus_min_bridges=1)
    assert ok.w_dict == 0.0 and ok.consensus_min_bridges == 1
