import shutil, pytest
from pathlib import Path
from contextos.config_dim.corpus_scope import scoped_hits, subset_prefixes


def test_subset_prefixes_from_map():
    m = {"business_docs": ["activity_document"], "customer_dict": ["dict"]}
    assert subset_prefixes(["customer_dict"], m) == ["dict"]
    assert set(subset_prefixes(["business_docs", "customer_dict"], m)) == {"activity_document", "dict"}


@pytest.mark.skipif(shutil.which("rg") is None, reason="需 ripgrep")
def test_no_cross_corpus(tmp_path):
    # 不串库: corpora=[customer_dict] 只返 dict/ 前缀命中
    (tmp_path / "activity_document").mkdir()
    (tmp_path / "dict").mkdir()
    (tmp_path / "activity_document" / "a.md").write_text("PM_OFFER_CHA 配置表", encoding="utf-8")
    (tmp_path / "dict" / "d.md").write_text("PM_OFFER_CHA 配置表字典", encoding="utf-8")
    m = {"business_docs": ["activity_document"], "customer_dict": ["dict"]}
    hits = scoped_hits(["PM_OFFER_CHA"], tmp_path, ["customer_dict"], m)
    assert hits and all(h.rel_path.startswith("dict/") for h in hits)
