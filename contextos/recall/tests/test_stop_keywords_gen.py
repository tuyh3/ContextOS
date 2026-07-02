"""设计思路: 一趟扫源码算 document-frequency(每词出现在多少个 .java 文件), df 超阈值 =
过宽候选。复用 keyword_extract 的抽取正则保证与过滤口径一致。评分标准: 高频词进候选、
低频不进、df 计数准、阈值边界、只扫 .java、exclude_dirs 生效、草稿不覆盖已激活客户文件。"""
import pathlib


def _mk_tree(root: pathlib.Path, n_hi=5):
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n_hi):
        (root / f"F{i}.java").write_text("class F { void m(){ FOOSVC.x(); DynamicCharging.y(); } }", encoding="utf-8")
    (root / "One.java").write_text("class One { BarOnce z; }", encoding="utf-8")
    (root / "skip").mkdir(exist_ok=True)
    (root / "skip" / "S.java").write_text("class S { EXCLUDEDME e; }", encoding="utf-8")


def test_derive_candidates_by_df(tmp_path):
    from contextos.recall.stop_keywords_gen import derive_stop_keyword_candidates
    src = tmp_path / "proj"
    _mk_tree(src)
    cands = dict(derive_stop_keyword_candidates([src], exclude_dirs=["skip"], min_files=3, min_df_ratio=0.5))
    assert cands.get("FOOSVC", 0) >= 5      # 高频 -> 候选, df=5
    assert "BARONCE" not in cands           # 低频(1 文件) -> 不进
    assert "EXCLUDEDME" not in cands        # exclude_dirs 生效


def test_render_draft_format():
    from contextos.recall.stop_keywords_gen import render_draft
    text = render_draft([("FOOSVC", 8231), ("WIDGET", 40)])
    assert "FOOSVC" in text and "8231" in text
    assert text.splitlines()[0].startswith("#")   # 顶部注释块
    # 倒序: 高 df 在前
    assert text.index("FOOSVC") < text.index("WIDGET")


def test_write_draft_never_overwrites_customer(tmp_path):
    from contextos.recall.stop_keywords_gen import write_draft
    src = tmp_path / "proj"
    _mk_tree(src)
    data_dir = tmp_path / "database"
    data_dir.mkdir()
    customer = data_dir / "stop-keywords.customer.txt"
    customer.write_text("KEEPME\n", encoding="utf-8")
    count, draft = write_draft([src], exclude_dirs=[], data_dir=data_dir, min_files=3, min_df_ratio=0.5)
    assert draft.name == "stop-keywords.draft.txt"
    assert draft.exists()
    assert customer.read_text(encoding="utf-8") == "KEEPME\n"   # 客户文件未被动
