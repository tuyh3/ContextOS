def test_leakage_no_longer_blanket_blocks_xlsx():
    """2026-06-10: 旧红线 #2 的 xlsx blanket 后缀拒已移除, gate 默认放行 xlsx。

    (xlsx 能否进语料另由 materialize 的 format filter 把关 —— 默认 formats 不含 xlsx,
    materialization 路径留 v2; 见 leakage.py docstring + 2026-06-06 红线#2改造 决策。)
    """
    from contextos.corpus.leakage import LeakageGate
    g = LeakageGate()
    assert g.is_allowed("document/business/a.md")
    assert g.is_allowed("data/fpa/result.xlsx")
    assert g.is_allowed("X.XLS")
    assert g.is_allowed("deep/dir/Y.XlSx")
    # 通用后缀拒仍可显式启用(机制保留)
    assert not LeakageGate(deny_suffixes=(".xlsx",)).is_allowed("data/fpa/result.xlsx")


def test_leakage_blocks_changelog_via_regex():
    from contextos.corpus.leakage import LeakageGate
    g = LeakageGate(exclude_regexes=["change-log/"])
    assert not g.is_allowed("activity/change-log/2022.md")
    assert g.is_allowed("activity/notes/2022.md")


def test_leakage_multiple_regexes():
    from contextos.corpus.leakage import LeakageGate
    g = LeakageGate(exclude_regexes=["change-log/", r"_gold\.md$"])
    assert not g.is_allowed("x/_gold.md")
    assert not g.is_allowed("change-log/a.md")
    assert g.is_allowed("x/normal.md")
