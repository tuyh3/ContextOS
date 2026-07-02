def test_default_stop_list_has_no_customer_categories():
    """通用 default 只留 7 通用类, 删客户特定 2 类(ZONG/CRM 等移出 tracked)。"""
    from contextos.recall.keyword_extract import load_stop_list
    stop = load_stop_list()
    # 通用词仍在
    assert "SELECT" in stop
    assert "ACTIVE" in stop
    assert "CREATE_DATE" in stop
    # 客户特定词已移出 tracked default
    assert "ZONG" not in stop
    assert "PAK" not in stop
    assert "CRM" not in stop
    assert "BSS" not in stop
    assert "BOSS" not in stop


def test_default_json_at_new_path():
    from contextos.recall.keyword_extract import DEFAULT_STOP_LIST
    assert DEFAULT_STOP_LIST.name == "default.json"
    assert DEFAULT_STOP_LIST.parent.name == "stop_keywords"
    assert DEFAULT_STOP_LIST.exists()


def test_parse_customer_stop_first_token():
    """客户 .txt: 行首# 注释跳过; 每行取第一个空白分隔字段(草稿带 df 标注可直接用)。"""
    from contextos.recall.keyword_extract import _parse_customer_stop
    text = "# comment\n\nFOOSVC 8231\nBAR  # inline note\nBaz\n"
    assert _parse_customer_stop(text) == ["FOOSVC", "BAR", "BAZ"]


def test_load_stop_list_merges_default_and_customer(tmp_path):
    from contextos.recall.keyword_extract import load_stop_list
    cust = tmp_path / "cust.txt"
    cust.write_text("MYSUBSYS 999\nWIDGETX\n", encoding="utf-8")
    merged = load_stop_list(customer_path=str(cust))
    assert "SELECT" in merged        # default 仍在
    assert "MYSUBSYS" in merged      # customer 合入
    assert "WIDGETX" in merged


def test_load_stop_list_missing_customer_uses_default_only(tmp_path):
    from contextos.recall.keyword_extract import load_stop_list
    merged = load_stop_list(customer_path=str(tmp_path / "nope.txt"))
    assert "SELECT" in merged
    assert "MYSUBSYS" not in merged


def test_load_stop_list_missing_default_returns_empty(tmp_path):
    from contextos.recall.keyword_extract import load_stop_list
    assert load_stop_list(default_path=str(tmp_path / "nope.json")) == set()


def test_example_stop_keywords_parses_and_is_synthetic():
    """tracked 样例只放合成占位词(演示格式), 不含真实客户/行业词。"""
    from contextos.recall.keyword_extract import _parse_customer_stop, REPO_ROOT
    p = REPO_ROOT / "examples" / "telecom-bss" / "stop-keywords.txt"
    assert p.exists()
    terms = _parse_customer_stop(p.read_text(encoding="utf-8"))
    assert terms, "样例至少含一个示意词"
    for banned in ("CRM", "BSS", "BOSS", "OSP", "MKT", "ZONG", "PAK", "PAKISTAN"):
        assert banned not in terms, f"tracked 样例禁含真实客户/行业词: {banned}"
