"""Loader covers path precedence + env override on storage.data_dir."""
from __future__ import annotations

from pathlib import Path

import pytest

from contextos.profile.loader import ProfileNotFound, load_profile


_TOML = """
[llm]
provider = "claude"
api_key_env = "ANTHROPIC_API_KEY"

[embedding]
model = "BAAI/bge-m3"

[reranker]
enabled = true
model = "BAAI/bge-reranker-v2-m3"
top_k_input = 50
top_k_output = 10

[query_expansion]
enabled = true
translation_provider = "main_llm"
fallback_provider = "local_qwen_2_5_7b"

[storage]
data_dir = "{data_dir}"

[ingestion]
default_cleanup = "full"
chunk_strategy = "h2_h3"
min_chunk_chars = 30

[jdtls_runtime]
jdtls_path = "/jdtls"
lombok_path = "/jdtls/lombok.jar"
java_home = "/jre21"

[oracle]
tns_admin = "/tns"
allowed_instances = ["TEST_DB1"]

[[projects]]
name = "demoproj"
path = "/demoproj"
language = "java"
build_system = "gradle"
"""


def _write_profile(path: Path, data_dir: str = "/tmp/data") -> None:
    path.write_text(_TOML.format(data_dir=data_dir), encoding="utf-8")


def test_load_profile_from_explicit_path(tmp_path: Path) -> None:
    p = tmp_path / "profile.toml"
    _write_profile(p)
    profile = load_profile(p)
    assert profile.storage.data_dir == "/tmp/data"


def test_env_override_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "profile.toml"
    _write_profile(p, data_dir="/tmp/from-toml")
    monkeypatch.setenv("CONTEXTOS_DATA_DIR", "/tmp/from-env")
    profile = load_profile(p)
    assert profile.storage.data_dir == "/tmp/from-env"


def test_load_profile_precedence_env_path_then_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd_profile = tmp_path / "profile.toml"
    _write_profile(cwd_profile, data_dir="/tmp/cwd")
    env_profile = tmp_path / "alt" / "profile.toml"
    env_profile.parent.mkdir()
    _write_profile(env_profile, data_dir="/tmp/env")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONTEXTOS_PROFILE", str(env_profile))
    profile = load_profile()
    assert profile.storage.data_dir == "/tmp/env"


def test_load_profile_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CONTEXTOS_PROFILE", raising=False)
    monkeypatch.delenv("CONTEXTOS_DATA_DIR", raising=False)
    with pytest.raises(ProfileNotFound):
        load_profile()
