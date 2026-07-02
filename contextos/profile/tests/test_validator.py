"""Validator catches cross-namespace 配置违规 with actionable messages."""
from __future__ import annotations

from pathlib import Path

import pytest

from contextos.profile.schema import Profile
from contextos.profile.validator import ProfileValidationError, validate_profile


def _profile(**overrides) -> Profile:
    base: dict = {
        "llm": {"provider": "claude", "api_key_env": "ANTHROPIC_API_KEY"},
        "embedding": {"model": "BAAI/bge-m3"},
        "reranker": {"enabled": True, "model": "x",
                     "top_k_input": 50, "top_k_output": 10},
        "query_expansion": {"enabled": True,
                            "translation_provider": "main_llm",
                            "fallback_provider": "local_qwen"},
        "storage": {"data_dir": "/tmp/x"},
        "ingestion": {"default_cleanup": "full",
                      "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "/jdtls",
                          "lombok_path": "/jdtls/l.jar", "java_home": "/jre"},
        "oracle": {"tns_admin": "/tns",
                   "allowed_instances": ["TEST_DB1"]},
        "projects": [{"name": "demoproj", "path": "/demoproj",
                      "language": "java", "build_system": "gradle"}],
    }
    for k, v in overrides.items():
        base[k] = {**base.get(k, {}), **v} if isinstance(v, dict) else v
    return Profile(**base)


def test_oracle_rejects_production_keyword_in_allowed() -> None:
    bad = _profile(oracle={"tns_admin": "/tns",
                           "allowed_instances": ["MY_PROD_DB"]})
    with pytest.raises(ProfileValidationError, match="production keyword"):
        validate_profile(bad, check_paths=False)


def test_multi_keyword_tns_emits_single_aggregated_error() -> None:
    bad = _profile(oracle={"tns_admin": "/tns",
                           "allowed_instances": ["TEST_PROD_PRD_LIVE"]})
    with pytest.raises(ProfileValidationError) as exc_info:
        validate_profile(bad, check_paths=False)
    msg = str(exc_info.value)
    assert msg.count("with production keyword(s)") == 1
    assert "'PROD'" in msg and "'PRD'" in msg and "'LIVE'" in msg


def test_paths_checked_when_enabled(tmp_path: Path) -> None:
    bad = _profile(jdtls_runtime={"jdtls_path": str(tmp_path / "no-such"),
                                  "lombok_path": str(tmp_path / "no-such.jar"),
                                  "java_home": str(tmp_path / "no-jre")})
    with pytest.raises(ProfileValidationError, match="jdtls_path"):
        validate_profile(bad, check_paths=True)


def test_relative_path_rejected_when_paths_checked(tmp_path: Path) -> None:
    bad = _profile(jdtls_runtime={"jdtls_path": "./relative/jdtls",
                                  "lombok_path": str(tmp_path),
                                  "java_home": str(tmp_path)})
    with pytest.raises(ProfileValidationError, match="must be absolute"):
        validate_profile(bad, check_paths=True)


def test_multiple_violations_reported_together() -> None:
    # 两个白名单实例都带 prod 关键词 -> 两条聚合错误。
    bad = _profile(oracle={"tns_admin": "/tns",
                           "allowed_instances": ["MY_PROD_DB", "OTHER_LIVE_DB"]})
    with pytest.raises(ProfileValidationError) as exc_info:
        validate_profile(bad, check_paths=False)
    msg = str(exc_info.value)
    assert msg.count("with production keyword(s)") == 2
    assert "'PROD'" in msg and "'LIVE'" in msg


def test_validate_ok_when_paths_disabled() -> None:
    good = _profile()
    validate_profile(good, check_paths=False)


def test_jdtls_path_error_carries_remediation_hint(tmp_path: Path) -> None:
    """jdtls_runtime 路径错必须带补救指引(2026-07-02): 指到 `contextos health` 自动探测
    + README 下载指引, 不再只有裸"不存在"。"""
    bad = _profile(jdtls_runtime={"jdtls_path": str(tmp_path / "no-such"),
                                  "lombok_path": str(tmp_path / "no-such.jar"),
                                  "java_home": str(tmp_path / "no-jre")})
    with pytest.raises(ProfileValidationError) as exc_info:
        validate_profile(bad, check_paths=True)
    msg = str(exc_info.value)
    assert "contextos health" in msg and "README" in msg


def test_non_jdtls_path_error_has_no_jdtls_hint(tmp_path: Path) -> None:
    """只有 oracle.tns_admin 路径错时不该扯 jdtls 指引(hint 精准不泛滥)。"""
    for d in ("server", "jre"):
        (tmp_path / d).mkdir()
    (tmp_path / "l.jar").write_bytes(b"PK")
    proj = tmp_path / "demoproj"
    proj.mkdir()
    bad = _profile(
        jdtls_runtime={"jdtls_path": str(tmp_path / "server"),
                       "lombok_path": str(tmp_path / "l.jar"),
                       "java_home": str(tmp_path / "jre")},
        oracle={"tns_admin": str(tmp_path / "no-tns"),
                "allowed_instances": ["TEST_DB1"]},
        projects=[{"name": "demoproj", "path": str(proj),
                   "language": "java", "build_system": "gradle"}],
    )
    with pytest.raises(ProfileValidationError) as exc_info:
        validate_profile(bad, check_paths=True)
    msg = str(exc_info.value)
    assert "tns_admin" in msg
    assert "contextos health" not in msg
