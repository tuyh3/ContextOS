"""profile 测试夹具。

`make_profile`: 合成一个**中性值** Profile(不掺真客户 schema/owner/db/实例名),
data_dir 默认指向 tmp_path 下的隔离目录。形态照抄 contextos/mcp_server/tests/
conftest.py 的 make_profile 工厂(同一套 9 namespace 必填字段中性范式)。

oracle 命名空间填的 TEST_DB1 是白名单**枚举占位**(与真客户连接无关:
connect_from_profile 在缺 ORACLE_<TNS>_USER/_PASSWORD 凭据时即 OracleSafetyError,
不会真连网),allowed_instances 至少 1 项是 schema min_length 约束所需。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from contextos.profile.schema import Profile


@pytest.fixture
def make_profile(tmp_path: Path):
    """返回一个工厂:make_profile(data_dir=...) -> 中性合成 Profile。

    不传 data_dir 时落在 tmp_path/contextos-data(测试隔离)。所有路径/实例名
    都是合成中性值,绝不含真客户标识(守 feedback_offline_test_neutral_fixtures)。
    """

    def _make(*, data_dir: Path | None = None) -> Profile:
        dd = data_dir if data_dir is not None else (tmp_path / "contextos-data")
        return Profile.model_validate({
            "llm": {"provider": "test_llm", "api_key_env": "PROFILE_TEST_LLM_KEY"},
            "embedding": {"model": "test-embed"},
            "reranker": {"enabled": True, "model": "test-rerank",
                         "top_k_input": 50, "top_k_output": 10},
            "query_expansion": {"enabled": True,
                                "translation_provider": "main_llm",
                                "fallback_provider": "local"},
            "storage": {"data_dir": str(dd)},
            "ingestion": {"default_cleanup": "full",
                          "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
            "jdtls_runtime": {"jdtls_path": "/jdtls",
                              "lombok_path": "/jdtls/lombok.jar",
                              "java_home": "/jre"},
            "oracle": {"tns_admin": "/tns",
                       "allowed_instances": ["TEST_DB1"]},
            "projects": [{"name": "proj", "path": "/proj",
                          "language": "java", "build_system": "gradle"}],
        })

    return _make
