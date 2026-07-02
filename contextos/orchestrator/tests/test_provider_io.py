"""U0 统一 provider 输出契约测试。对齐 08 §2 信封 SSOT。"""
import pytest
from pydantic import ValidationError


def test_provider_candidate_minimal_and_signals_open():
    from contextos.orchestrator.provider_io import ProviderCandidate
    c = ProviderCandidate(target="a.b.C#m()", kind="METHOD")
    assert c.signals == {}
    c2 = ProviderCandidate(target="x", kind="CLASS", signals={"name_match_strength": 1.0})
    assert c2.signals["name_match_strength"] == 1.0


def test_provider_result_defaults():
    from contextos.orchestrator.provider_io import ProviderResult
    r = ProviderResult(worker_name="code_search", score=0.85)
    assert r.candidates == []
    assert r.score_breakdown == {}
    assert r.reasoning == ""
    assert r.miss_reason is None


def test_provider_result_score_range_validated():
    from contextos.orchestrator.provider_io import ProviderResult
    with pytest.raises(ValidationError):
        ProviderResult(worker_name="rag", score=1.5)
    with pytest.raises(ValidationError):
        ProviderResult(worker_name="rag", score=-0.1)


def test_provider_result_extra_forbidden():
    from contextos.orchestrator.provider_io import ProviderResult
    with pytest.raises(ValidationError):
        # model_validate(dict) form: tests extra=forbid without a static-typing
        # call error on the deliberate bad kwarg (matches the codebase convention).
        ProviderResult.model_validate({"worker_name": "x", "score": 0.1, "bogus": "nope"})


def test_provider_result_miss_helper():
    """08 §5.1 失败传播:miss -> score=0 + 空候选 + miss_reason。"""
    from contextos.orchestrator.provider_io import ProviderResult
    r = ProviderResult.miss("code_search", "jdtls_unavailable")
    assert r.worker_name == "code_search"
    assert r.score == 0.0
    assert r.candidates == []
    assert r.miss_reason == "jdtls_unavailable"
    assert r.score_breakdown == {}
    assert r.reasoning == ""


def test_worker_name_is_open_not_enum():
    """08 §2 扩展性铁律:worker_name 是开放 str,加 provider 不改框架。
    未注册的桥名(v2 git_evidence / 任意未来桥)必须被接受,不能是 Literal 闭枚举。"""
    from contextos.orchestrator.provider_io import ProviderResult
    for name in ("code_search", "rag", "db_lineage_bridge",
                 "config_dimension_bridge", "git_evidence", "some_future_bridge_v3"):
        r = ProviderResult(worker_name=name, score=0.5)
        assert r.worker_name == name
