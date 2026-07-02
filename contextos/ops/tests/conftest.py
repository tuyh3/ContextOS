"""ops 组件 B 测试夹具。中性合成值(无真客户标识, 守 feedback_offline_test_neutral_fixtures)。

make_ops_profile(data_dir=...) -> Profile: 9 namespace 必填 + 中性值。
fake_ops_app_ctx: AppContext duck-typed 替身, 暴露 recorder 消费的 profile + engine。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from contextos.profile.schema import Profile


@pytest.fixture
def make_ops_profile(tmp_path: Path):
    def _make(*, data_dir: Path | None = None, materialized_dir: str = "") -> Profile:
        dd = data_dir if data_dir is not None else (tmp_path / "contextos-data")
        return Profile(**{
            "llm": {"provider": "test_llm", "api_key_env": "OPS_TEST_LLM_KEY"},
            "embedding": {"model": "test-embed"},
            "reranker": {"enabled": True, "model": "test-rerank",
                         "top_k_input": 50, "top_k_output": 10},
            "query_expansion": {"enabled": True,
                                "translation_provider": "main_llm",
                                "fallback_provider": "local"},
            "storage": {"data_dir": str(dd)},
            "ingestion": {"default_cleanup": "full",
                          "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
            "corpus": {"materialized_dir": materialized_dir},
            "jdtls_runtime": {"jdtls_path": "/jdtls",
                              "lombok_path": "/jdtls/lombok.jar",
                              "java_home": "/jre"},
            "oracle": {"tns_admin": "/tns",
                       "allowed_instances": ["TEST_DB1"]},
            "projects": [{"name": "proj", "path": "/proj",
                          "language": "java", "build_system": "gradle"}],
        })
    return _make


class _FakeOpsAppCtx:
    """recorder 消费的最小替身: profile + 真内存 SQLite engine(sidecar 表用)。"""

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.engine = create_engine("sqlite://")


@pytest.fixture
def fake_ops_app_ctx(make_ops_profile):
    return _FakeOpsAppCtx(make_ops_profile())
