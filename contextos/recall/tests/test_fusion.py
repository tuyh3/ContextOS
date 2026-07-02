def test_rrf_merge_combines_rankings():
    from contextos.recall.fusion import rrf_merge
    # 两路排名(每路 = 有序 doc id 列表)
    sparse = ["docA", "docB", "docC"]
    dense = ["docB", "docA", "docD"]
    merged = rrf_merge([sparse, dense], k=60)
    # 返回 (doc, score) 按分降序; docA/docB 出现在两路 -> 分更高
    ids = [d for d, _ in merged]
    assert set(ids) == {"docA", "docB", "docC", "docD"}
    assert ids[0] in {"docA", "docB"}        # 双路命中的排前
    assert ids[-1] in {"docC", "docD"}       # 单路命中的靠后


def test_rrf_merge_single_ranking():
    from contextos.recall.fusion import rrf_merge
    merged = rrf_merge([["x", "y"]], k=60)
    assert [d for d, _ in merged] == ["x", "y"]


def test_rrf_merge_empty():
    from contextos.recall.fusion import rrf_merge
    assert rrf_merge([], k=60) == []
