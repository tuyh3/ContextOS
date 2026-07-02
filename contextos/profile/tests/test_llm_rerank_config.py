"""Plan 07 旋钮接 profile (2026-06-09): LlmRerankConfig namespace + 向后兼容默认。

设计思路: 07 LLM 重排(provider.rerank)的运行旋钮(batch_size / max_concurrency / 三维 cap)
此前写死在 contextos.rerank.schema.RerankConfig 默认值里, 客户不改代码调不了。本 namespace 把它们
暴露到 profile.toml 的 [llm_rerank] 段(区别于 [reranker] = 03 BGE 重排), 默认值与 RerankConfig 对齐,
故既有 profile(无该段)向后兼容。映射到 RerankConfig 由 build_impact_map_impl 的 helper 做(见
test_impact_map_rerank_config.py)。

评分标准(自动): 默认值对齐 RerankConfig / 向后兼容加载 / override 生效 / 坏边界拒。中性合成 fixture
(守 feedback_offline_test_neutral_fixtures)。
"""
from __future__ import annotations

import pytest

from contextos.profile.schema import LlmRerankConfig, Profile


def _minimal_profile_dict() -> dict:
    """最小合法 Profile dict(无 [llm_rerank] 段), 镜像 test_corroboration_config 的中性 fixture。"""
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


def test_llm_rerank_defaults():
    c = LlmRerankConfig()
    assert c.batch_size == 8            # 对齐 RerankConfig 默认(防真 DeepSeek 逐候选串行超时)
    assert c.max_concurrency == 6       # chunk 并发上限(线程池)
    assert (c.method_cap, c.sql_cap, c.config_cap) == (30, 30, 20)


def test_llm_rerank_strict_extra_forbidden():
    with pytest.raises(Exception):
        LlmRerankConfig.model_validate({"batch_size": 4, "bogus": 1})


def test_profile_has_llm_rerank_default():
    # 既有 profile(无 [llm_rerank] 段)仍可加载 -> default_factory 向后兼容
    p = Profile.model_validate(_minimal_profile_dict())
    assert isinstance(p.llm_rerank, LlmRerankConfig)
    assert p.llm_rerank.batch_size == 8 and p.llm_rerank.max_concurrency == 6


def test_profile_llm_rerank_override():
    d = _minimal_profile_dict()
    d["llm_rerank"] = {"batch_size": 4, "max_concurrency": 3, "method_cap": 20,
                       "sql_cap": 15, "config_cap": 10}
    p = Profile.model_validate(d)
    assert p.llm_rerank.batch_size == 4 and p.llm_rerank.max_concurrency == 3
    assert (p.llm_rerank.method_cap, p.llm_rerank.sql_cap, p.llm_rerank.config_cap) == (20, 15, 10)


def test_llm_rerank_rejects_bad_bounds():
    # batch_size/max_concurrency >=1(0 会让线程池/分块退化), cap >=0(负 cap -> items[:-N] drop-last 怪语义)
    for bad in ({"batch_size": 0}, {"max_concurrency": 0}, {"method_cap": -1},
                {"sql_cap": -1}, {"config_cap": -1}):
        with pytest.raises(Exception):
            LlmRerankConfig(**bad)
    # 合法边界: cap=0(该维全 skip), batch/concurrency=1(逐候选/串行)
    ok = LlmRerankConfig(batch_size=1, max_concurrency=1, method_cap=0)
    assert ok.batch_size == 1 and ok.max_concurrency == 1 and ok.method_cap == 0
