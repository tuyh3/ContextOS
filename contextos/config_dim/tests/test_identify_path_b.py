from contextos.config_dim.identify import path_b_query, build_comment_sql


def test_build_comment_sql_uses_bind_params():
    sql, params = build_comment_sql(["UPC"], ["配置", "规则"], ["config", "switch"])
    assert "ALL_TAB_COMMENTS" in sql.upper()
    assert ":kw0" in sql and ":o0" in sql          # bind 占位符, 关键词/owner 不拼进 SQL
    assert "配置" not in sql and "config" not in sql
    assert params["o0"] == "UPC"
    assert any("config" in v for v in params.values())


def test_build_comment_sql_injection_safe():
    # 恶意关键词走 bind 不破坏 SQL(Plan 05 #4 同类防护)
    sql, params = build_comment_sql(["UPC"], ["x' OR '1'='1"], [])
    assert "OR '1'='1" not in sql                       # 没拼进 SQL
    assert any("1'='1" in v for v in params.values())   # 当 LIKE 字面量在 param


def test_path_b_parses_rows():
    # Fake executor: 返 (owner, table, comments) 行
    def fake_exec(db, sql, **kw):
        return [{"OWNER": "UPC", "TABLE_NAME": "PM_OFFER_CHA", "COMMENTS": "Offer 渠道配置表"}]
    hits = path_b_query(fake_exec, db="CTEST", owners=["UPC"],
                        kw_zh=["配置"], kw_en=["config"])
    assert hits["UPC.PM_OFFER_CHA"]["confidence"] == "high"
    assert "配置" in hits["UPC.PM_OFFER_CHA"]["excerpt"]
