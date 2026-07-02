def test_rag_config_defaults():
    from contextos.profile.schema import RagConfig
    c = RagConfig()
    assert c.dense_enabled is False           # dense 默认关(gate 决定开)
    assert c.reranker_backend == "fake"
    assert c.window_radius >= 1
    assert c.max_passages_per_doc >= 1


def test_rag_config_reranker_backend_enum():
    import pytest
    from pydantic import ValidationError
    from contextos.profile.schema import RagConfig
    RagConfig(reranker_backend="bge")          # 合法
    with pytest.raises(ValidationError):
        RagConfig(reranker_backend="nope")


def test_profile_rag_optional_backward_compatible():
    from contextos.profile.schema import Profile
    p = Profile(
        llm={"provider": "x", "api_key_env": "K"},
        embedding={"model": "m"},
        reranker={"model": "r"},
        query_expansion={"translation_provider": "a", "fallback_provider": "b"},
        storage={"data_dir": "/tmp/d"},
        ingestion={},
        jdtls_runtime={"jdtls_path": "/j", "lombok_path": "/l", "java_home": "/h"},
        oracle={"tns_admin": "/t", "allowed_instances": ["TEST_X"]},
        projects=[{"name": "p", "path": "/p", "language": "java"}],
    )
    assert p.rag.dense_enabled is False
