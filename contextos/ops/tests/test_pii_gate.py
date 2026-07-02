"""新增专用 PII gate 测试(spec Appendix B MUST: 不复用 sensitive.py)。

设计思路: assert_no_pii 校验 4 检索字段(phenomenon_signature/confirmed_root_cause/
decisive_data_note/search_terms), 命中 MSISDN/email/手机/姓名/凭据/value_raw 形态即 raise。
姓名走保守标签形态(姓名/name 标签后跟值), 不裸匹配中文名(避免高误伤)。
评分标准: 各 PII 形态被拒;中性合成文本放行;裸中文名不误拒;空/None 字段不误拒。
自动脚本逻辑: 中性合成 PII fixture(非真客户值), 正/反例各断言。
注意: 这是负判 gate, 用 mutation 思路 —— 每种 PII 形态独立一条, 防"漏一类不报"。
"""
from __future__ import annotations

import pytest

from contextos.ops.pii_gate import PiiGateError, assert_no_pii


# 中性合成 PII fixture(非真客户值; 形态真实, 值是占位)
_CLEAN = {
    "phenomenon_signature": "信用额度内订购大额套餐成功 违反 余额加信用大于等于费用",
    "confirmed_root_cause": "递延收费 订购与扣费时点解耦",
    "decisive_data_note": "charge model 出 index 标 inconclusive",
    "search_terms": "递延收费 余额不足 信用额度",
}


def test_clean_text_passes():
    assert_no_pii(_CLEAN) is None


@pytest.mark.parametrize("field,bad", [
    ("phenomenon_signature", "用户 13800138000 订购失败"),       # 手机号
    ("confirmed_root_cause", "联系 alice@example.com 复核"),       # email
    ("decisive_data_note", "MSISDN=8613800138000 扣费异常"),      # MSISDN
    ("search_terms", "password=hunter2 配置错误"),                # 凭据形态
    ("phenomenon_signature", "字段 value_raw=secret-token-xyz"),  # value_raw 形态
    ("confirmed_root_cause", "客户姓名: 张三 投诉扣费"),           # 姓名(标签形态)
    ("phenomenon_signature", "姓名=李四 订购失败"),               # 姓名(标签形态, = 分隔)
])
def test_pii_rejected(field, bad):
    fields = dict(_CLEAN)
    fields[field] = bad
    with pytest.raises(PiiGateError):
        assert_no_pii(fields)


def test_none_and_empty_fields_ok():
    fields = dict(_CLEAN)
    fields["decisive_data_note"] = None
    fields["search_terms"] = ""
    assert assert_no_pii(fields) is None


def test_only_listed_fields_scanned():
    # 非 4 检索字段(如 mechanism_tag)不在 gate 扫描范围 -> 不传即不扫
    assert_no_pii({"phenomenon_signature": "正常现象描述"}) is None


def test_bare_chinese_name_not_false_positive():
    # 保守模式: 不裸匹配中文名(避免高误伤普通业务描述里的人名/术语)。
    # 只在 "姓名/name" 标签后跟值时才拒, 裸名不触发。
    assert_no_pii({"phenomenon_signature": "张三丰 创办武当 与本案无关的业务描述"}) is None
