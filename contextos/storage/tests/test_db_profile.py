"""engine_from_profile uses storage.data_dir to derive SQLite path by default."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from contextos.profile.schema import Profile
from contextos.storage.db import engine_from_profile


def _profile(tmp_path: Path) -> Profile:
    return Profile.model_validate({
        "llm": {"provider": "claude", "api_key_env": "K"},
        "embedding": {"model": "BAAI/bge-m3"},
        "reranker": {"enabled": True, "model": "x",
                     "top_k_input": 50, "top_k_output": 10},
        "query_expansion": {"enabled": True,
                            "translation_provider": "main_llm",
                            "fallback_provider": "local"},
        "storage": {"data_dir": str(tmp_path)},
        "ingestion": {"default_cleanup": "full",
                      "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "/j",
                          "lombok_path": "/j/l.jar", "java_home": "/jre"},
        "oracle": {"tns_admin": "/tns",
                   "allowed_instances": ["TEST_DB1"]},
        "projects": [{"name": "demoproj", "path": "/c",
                      "language": "java", "build_system": "gradle"}],
    })


def test_engine_from_profile_creates_sqlite_under_data_dir(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    engine = engine_from_profile(profile)
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE smoke (id INTEGER)"))
        conn.execute(text("INSERT INTO smoke VALUES (1)"))
        assert conn.execute(text("SELECT id FROM smoke")).scalar() == 1
    assert (tmp_path / "contextos.db").exists()
