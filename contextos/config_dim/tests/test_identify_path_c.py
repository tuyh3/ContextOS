from contextos.config_dim.identify import has_config_signal, path_c_query


def test_has_config_signal():
    assert has_config_signal("PM_OFFER_CHA 是渠道配置表", ["配置", "规则"], ["config"])
    assert has_config_signal("see config table", ["配置"], ["config"])
    assert not has_config_signal("PM_OFFER_CHA 客户主表", ["配置", "规则"], ["config"])


def test_path_c_with_fake_search():
    class Hit:
        def __init__(self, line): self.line = line; self.rel_path = "activity_document/a.md"
    def fake_search(patterns, subsets):
        return [Hit("PM_OFFER_CHA 是渠道授权配置表")]
    hits = path_c_query("PM_OFFER_CHA", fake_search, kw_zh=["配置"], kw_en=["config"])
    assert hits and hits["confidence"] in ("high", "medium")
    assert "配置" in hits["excerpt"]
