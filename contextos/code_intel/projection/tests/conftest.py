"""projection 测试夹具。

`make_profile`: 照抄 contextos/profile/tests/conftest.py 的中性值工厂(9 namespace
必填字段, 不掺真客户标识), 加三个 04b 可选参数:
- project_path: 覆盖 projects[0].path(Profile 没有 `project` 字段, 是 projects list)
- source_roots: 覆盖 code.source_roots(空列表语义 = 扫 project.path 整仓)
- extra_classpath_dirs: 覆盖 code_index.extra_classpath_dirs
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from contextos.profile.schema import Profile


@pytest.fixture
def engine() -> Engine:
    return create_engine("sqlite://", future=True)


@pytest.fixture
def make_profile(tmp_path: Path):
    """返回一个工厂:make_profile(...) -> 中性合成 Profile。

    不传 data_dir 时落在 tmp_path/contextos-data(测试隔离)。所有路径/实例名
    都是合成中性值,绝不含真客户标识(守 feedback_offline_test_neutral_fixtures)。
    """

    def _make(
        *,
        data_dir: Path | None = None,
        project_path: str | None = None,
        source_roots: list[str] | None = None,
        extra_classpath_dirs: list[str] | None = None,
    ) -> Profile:
        dd = data_dir if data_dir is not None else (tmp_path / "contextos-data")
        base: dict[str, Any] = {
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
        }
        if project_path is not None:
            base["projects"][0]["path"] = project_path
        if source_roots is not None:
            base.setdefault("code", {"source_roots": [], "exclude_dirs": []})
            base["code"]["source_roots"] = source_roots
        if extra_classpath_dirs is not None:
            base.setdefault("code_index", {})
            base["code_index"]["extra_classpath_dirs"] = extra_classpath_dirs
        return Profile.model_validate(base)

    return _make
