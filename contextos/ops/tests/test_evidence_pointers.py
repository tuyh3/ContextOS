"""evidence_pointers 白名单前缀 fail-closed 测试(spec Appendix B MUST)。

设计思路: 只收 fqn:<点号FQN> / table:OWNER.TABLE / config:<key.path>;
拒裸 SQL / 文件路径 / URL / 行数据 / literal value / 其它前缀。fqn 点号形对齐 middleware _FQN_RE。
评分标准: 三类合法前缀放行;非法前缀 + 形态错误(fqn 带 # / table 小写 / 裸路径)整调用 reject。
自动脚本逻辑: 正/反例 parametrize;反例覆盖 spec 列举的各拒绝类。
"""
from __future__ import annotations

import pytest

from contextos.ops.evidence_pointers import EvidencePointerError, validate_pointers


@pytest.mark.parametrize("ptr", [
    "fqn:com.example.service.BalanceService.deduct",
    "fqn:com.example.Foo.<init>",
    "table:APP.SERVICE_BALANCE",
    "config:billing.deferred.enabled",
])
def test_valid_pointers_pass(ptr):
    validate_pointers([ptr])  # 不抛


@pytest.mark.parametrize("ptr", [
    "SELECT * FROM ORDERS",                       # 裸 SQL
    "/src/main/java/Foo.java",                    # 文件路径
    "http://example.com/x",                       # URL
    "fqn:com.example.Foo#method",                 # fqn 带 # (middleware 拒)
    "table:app.lower_case",                       # table 非大写
    "table:NOSCHEMA",                             # table 缺 OWNER.TABLE 点号
    "literal:9999",                               # 非白名单前缀
    "com.example.Foo.bar",                        # 无前缀
    "",                                           # 空
])
def test_invalid_pointers_rejected(ptr):
    with pytest.raises(EvidencePointerError):
        validate_pointers([ptr])


def test_empty_list_ok():
    validate_pointers([])  # 允许无指针(decisive_data_note 可能是唯一证据)


def test_one_bad_rejects_whole_list():
    with pytest.raises(EvidencePointerError):
        validate_pointers(["fqn:com.example.Foo.bar", "literal:bad"])
