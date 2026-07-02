from contextos.config_dim.identify import path_a_score, load_default_name_patterns


def test_seed_is_domain_neutral():
    pats = load_default_name_patterns()
    assert any("CONFIG" in p for p in pats)
    # 不带客户业务词
    assert not any(x in " ".join(pats).upper() for x in ["PRICING", "DISPATCH", "TARIFF"])


def test_path_a_name_and_rule_columns():
    pats = ["_CONFIG", "_PARAM", "_WHITELIST"]
    rule_cols = {"EFFECTIVE_DATE", "STATUS", "PRIORITY"}
    # 表名命中 -> low
    s1, ev1 = path_a_score("SYS_CONFIG", [], pats, rule_cols)
    assert s1 > 0 and ev1["name_hit"]
    # 有 >=2 规则列 -> medium 信号
    s2, ev2 = path_a_score("CB_FOO", ["EFFECTIVE_DATE", "STATUS", "X"], pats, rule_cols)
    assert ev2["rule_columns_hit"] >= 2
    # 都不命中 -> 0
    s3, _ = path_a_score("CB_CUSTOMER", ["NAME", "AGE"], pats, rule_cols)
    assert s3 == 0
