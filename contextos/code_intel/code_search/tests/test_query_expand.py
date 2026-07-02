"""搜索词扩展测试。专治 02 复合名撞不上真符号(手测 2026-06-01 实证)。"""
from contextos.code_intel.code_search.query_expand import expand_search_term


def test_three_word_camel_drops_to_two_word_core():
    # 真实失败链:DynamicChargingBatch 撞不上, DynamicCharging 能撞上
    assert expand_search_term("DynamicChargingBatch") == [
        "DynamicChargingBatch", "DynamicCharging",
    ]


def test_four_word_camel_drops_progressively_longest_first():
    assert expand_search_term("DynamicChargingBatchOperation") == [
        "DynamicChargingBatchOperation", "DynamicChargingBatch", "DynamicCharging",
    ]


def test_two_word_terms_not_decomposed():
    # 丢到 1 词(Dynamic / Sms)太宽, 不拆
    assert expand_search_term("DynamicCharging") == ["DynamicCharging"]
    assert expand_search_term("SmsReminder") == ["SmsReminder"]
    assert expand_search_term("SMS_REMINDER") == ["SMS_REMINDER"]


def test_single_word_unchanged():
    assert expand_search_term("Route") == ["Route"]
    assert expand_search_term("CustDbUtils") == [
        # Cust | Db | Utils = 3 词 -> 丢到 CustDb(2 词)
        "CustDbUtils", "CustDb",
    ]


def test_underscore_three_words_drops_trailing():
    # CONF_PROVINCE_TAX = 3 词 -> 丢尾得 CONF_PROVINCE(下划线保留)
    assert expand_search_term("CONF_PROVINCE_TAX") == [
        "CONF_PROVINCE_TAX", "CONF_PROVINCE",
    ]


def test_max_prefixes_caps_expansion():
    # 5 词, 默认 max_prefixes=2 -> 原词 + 2 前缀 = 3 串
    out = expand_search_term("OneTwoThreeFourFive")
    assert out[0] == "OneTwoThreeFourFive"
    assert len(out) == 3
    assert out == ["OneTwoThreeFourFive", "OneTwoThreeFour", "OneTwoThree"]


def test_max_prefixes_override():
    out = expand_search_term("OneTwoThreeFourFive", max_prefixes=1)
    assert out == ["OneTwoThreeFourFive", "OneTwoThreeFour"]


def test_empty_term():
    assert expand_search_term("") == [""]
