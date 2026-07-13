"""db_provider 测试夹具: 中性合成 Profile 工厂(oracle 型 / mysql 型各一)。

全中性值不掺真客户标识(feedback_offline_test_neutral_fixtures);
mysql 实例 alias=test_inst 对应凭据 env MYSQL_TEST_INST_USER/_PASSWORD(测试内 monkeypatch)。
"""
from __future__ import annotations

from typing import Any

import pytest

from contextos.profile.schema import Profile


def _base(database_section: dict[str, Any]) -> Profile:
    return Profile.model_validate({
        "llm": {"provider": "test_llm", "api_key_env": "PROFILE_TEST_LLM_KEY"},
        "embedding": {"model": "test-embed"},
        "reranker": {"enabled": True, "model": "test-rerank",
                     "top_k_input": 50, "top_k_output": 10},
        "query_expansion": {"enabled": True, "translation_provider": "main_llm",
                            "fallback_provider": "local"},
        "storage": {"data_dir": "/tmp/ctx-dbp-test"},
        "ingestion": {"default_cleanup": "full",
                      "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "/jdtls",
                          "lombok_path": "/jdtls/lombok.jar", "java_home": "/jre"},
        "projects": [{"name": "proj", "path": "/proj",
                      "language": "java", "build_system": "gradle"}],
        **database_section,
    })


@pytest.fixture
def make_oracle_profile():
    def _make() -> Profile:
        return _base({"oracle": {"tns_admin": "/tns",
                                 "allowed_instances": ["TEST_DB1"]}})
    return _make


@pytest.fixture
def make_mysql_profile():
    def _make() -> Profile:
        return _base({"database": {"type": "mysql", "mysql": {"instances": [{
            "alias": "test_inst", "host": "127.0.0.1", "port": 3306,
            "databases": ["appdb", "appdb_ext"],
        }]}}})
    return _make
