import shutil, pytest
from contextos.config_dim.corpus_scope import make_rag_search


@pytest.mark.skipif(shutil.which("rg") is None, reason="需 ripgrep")
def test_make_rag_search_2arg_closure(tmp_path):
    (tmp_path / "activity_document").mkdir()
    (tmp_path / "activity_document" / "a.md").write_text("PM_OFFER_CHA 配置表", encoding="utf-8")
    prefix_map = {"business_docs": ["activity_document"]}
    search = make_rag_search(tmp_path, prefix_map)
    hits = search(["PM_OFFER_CHA"], ["business_docs"])  # 2 参, 适配 path_c_query 注入
    assert hits and all(h.rel_path.startswith("activity_document/") for h in hits)
