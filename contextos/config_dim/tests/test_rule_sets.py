from contextos.config_dim.identify import identify_rule_set


def test_rule_set_by_rule_columns_and_category():
    # 有 >=2 规则列 + 名字含 _RULE -> rule_set 候选; category 从 profile map 推
    cat_map = {"_PRICING": "pricing", "_NOTIFY": "notification"}
    rs = identify_rule_set("CB_PRICING_RULE", ["EFFECTIVE_DATE", "STATUS", "AMOUNT"],
                           rule_columns={"EFFECTIVE_DATE", "STATUS"}, category_map=cat_map)
    assert rs is not None and rs["category"] == "pricing"
    # 无规则列 -> 非 rule_set
    assert identify_rule_set("CB_CUSTOMER", ["NAME"], {"STATUS"}, {}) is None
