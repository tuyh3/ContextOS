def test_fake_reranker_overlap_scores_order():
    from contextos.recall.reranker.fake import FakeReranker
    r = FakeReranker()
    q = "动态计费 dynamic charging"
    passages = [
        "无关内容 unrelated text",
        "动态计费由 DynamicChargingSVImpl 处理 dynamic charging",
    ]
    scores = r.score(q, passages)
    assert len(scores) == 2
    assert scores[1] > scores[0]            # 词重叠多的分更高


def test_fake_reranker_empty_passages():
    from contextos.recall.reranker.fake import FakeReranker
    assert FakeReranker().score("q", []) == []


def test_make_reranker_returns_fake():
    from contextos.profile.schema import RagConfig
    from contextos.recall.reranker import make_reranker
    from contextos.recall.reranker.base import Reranker
    r = make_reranker(RagConfig(reranker_backend="fake"))
    assert isinstance(r, Reranker)


def test_make_reranker_unknown_raises():
    import pytest
    from contextos.recall.reranker import make_reranker

    class _Cfg:
        reranker_backend = "bogus"

    with pytest.raises(ValueError):
        make_reranker(_Cfg())
