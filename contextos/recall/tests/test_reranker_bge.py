import pytest


@pytest.mark.integration
def test_bge_reranker_smoke():
    pytest.importorskip("sentence_transformers")
    from contextos.recall.reranker.bge import BGEReranker
    from contextos.recall.reranker.base import Reranker
    r = BGEReranker()  # 默认 BAAI/bge-reranker-v2-m3(首次会下载权重)
    assert isinstance(r, Reranker)
    scores = r.score("动态计费", ["动态计费由 DynamicChargingSVImpl 处理", "无关内容"])
    assert len(scores) == 2
    assert scores[0] > scores[1]
