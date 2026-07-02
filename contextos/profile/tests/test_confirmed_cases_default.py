"""confirmed-cases 内建固定 corpus 默认键测试(spec Appendix C MUST)。

设计思路: ConfigConfig model_validator 注入 "confirmed-cases": ["confirmed-cases"] 默认键,
使 rag_search(corpora=["confirmed-cases"]) 过 middleware 白名单。无论 profile 是否配
corpus_subset_prefixes, 该键都在(内建非 host 动态注册)。
评分标准: 空 profile 默认含键;客户配了别的子集仍补上 confirmed-cases;客户显式给了该键不被改坏。
自动脚本逻辑: 直接构造 ConfigConfig / Profile 断言 corpus_subset_prefixes 键。
"""
from __future__ import annotations

from contextos.profile.schema import ConfigConfig, Profile


def _min_profile(config: dict | None = None) -> Profile:
    return Profile(**{
        "llm": {"provider": "t", "api_key_env": "K"},
        "embedding": {"model": "m"},
        "reranker": {"enabled": True, "model": "r"},
        "query_expansion": {"enabled": True, "translation_provider": "a",
                            "fallback_provider": "b"},
        "storage": {"data_dir": "/tmp/x"},
        "ingestion": {"default_cleanup": "full", "chunk_strategy": "h2_h3",
                      "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "/j", "lombok_path": "/l", "java_home": "/h"},
        "oracle": {"tns_admin": "/t", "allowed_instances": ["TEST_DB1"]},
        "projects": [{"name": "p", "path": "/p", "language": "java"}],
        **({"config": config} if config else {}),
    })


def test_default_includes_confirmed_cases():
    cfg = ConfigConfig()
    assert cfg.corpus_subset_prefixes.get("confirmed-cases") == ["confirmed-cases"]


def test_profile_default_has_confirmed_cases():
    p = _min_profile()
    assert "confirmed-cases" in p.config.corpus_subset_prefixes


def test_customer_subsets_keep_confirmed_cases():
    p = _min_profile(config={"corpus_subset_prefixes": {"billing-docs": ["billing"]}})
    assert "billing-docs" in p.config.corpus_subset_prefixes
    assert p.config.corpus_subset_prefixes["confirmed-cases"] == ["confirmed-cases"]


def test_explicit_confirmed_cases_not_clobbered():
    p = _min_profile(config={"corpus_subset_prefixes":
                             {"confirmed-cases": ["confirmed-cases", "extra"]}})
    assert p.config.corpus_subset_prefixes["confirmed-cases"] == ["confirmed-cases", "extra"]
