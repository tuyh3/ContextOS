def test_corpus_config_all_defaults():
    """CorpusConfig 全默认可构造 -> 向后兼容。"""
    from contextos.profile.schema import CorpusConfig
    c = CorpusConfig()
    assert c.sources == []
    assert c.ocr.backend == "fake"
    assert c.materialized_dir == ""
    assert "docx" in c.formats and "png" in c.formats and "md" in c.formats


def test_source_config_fields():
    from contextos.profile.schema import SourceConfig
    s = SourceConfig(type="git", location="/x", glob=["**/*.md"],
                     leakage_exclude_regex=["change-log/"])
    assert s.type == "git"
    assert s.glob == ["**/*.md"]
    assert s.leakage_exclude_regex == ["change-log/"]


def test_source_config_glob_default():
    from contextos.profile.schema import SourceConfig
    s = SourceConfig(type="dir", location="/x")
    assert any(g.endswith("*.md") for g in s.glob)
    assert any(g.endswith("*.docx") for g in s.glob)


def test_ocr_config_backend_enum_rejects_unknown():
    import pytest
    from pydantic import ValidationError
    from contextos.profile.schema import OcrConfig
    with pytest.raises(ValidationError):
        OcrConfig.model_validate({"backend": "not_a_backend"})


def test_profile_corpus_optional_backward_compatible():
    """既有 Profile(无 corpus) 仍合法; corpus 默认填充。"""
    from contextos.profile.schema import Profile
    d = {
        "llm": {"provider": "x", "api_key_env": "K"},
        "embedding": {"model": "m"},
        "reranker": {"model": "r"},
        "query_expansion": {"translation_provider": "a", "fallback_provider": "b"},
        "storage": {"data_dir": "/tmp/d"},
        "ingestion": {},
        "jdtls_runtime": {"jdtls_path": "/j", "lombok_path": "/l", "java_home": "/h"},
        "oracle": {"tns_admin": "/t", "allowed_instances": ["TEST_X"]},
        "projects": [{"name": "p", "path": "/p", "language": "java"}],
    }
    p = Profile(**d)
    assert p.corpus.ocr.backend == "fake"
