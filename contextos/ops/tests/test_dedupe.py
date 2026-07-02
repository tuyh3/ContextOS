"""dedupe_key / case_id 测试(spec Appendix A + Appendix E [case_id 不撞])。

设计思路: dedupe_key = normalize(signature)+mechanism_tag(找候选);
case_id = sha256(dedupe_key + normalize(root_cause))(加 root_cause 区分)。
评分标准: normalize 归一(去空白/小写);同 signature+mechanism 不同 root_cause -> case_id 不同
(differential/conflict 共存不撞);同 dedupe_key + 同 root_cause -> case_id 相同(合并锚)。
自动脚本逻辑: 直接断言 normalize / key / id 的等价与区分。
"""
from __future__ import annotations

from contextos.ops.dedupe import compute_case_id, compute_dedupe_key, normalize


def test_normalize_collapses_whitespace_and_lowercases():
    assert normalize("  余额  不足 ") == normalize("余额 不足")
    assert normalize("Deferred  Charge") == normalize("deferred charge")


def test_dedupe_key_combines_signature_and_mechanism():
    k1 = compute_dedupe_key("余额不足导致订购失败", "deferred_charge")
    k2 = compute_dedupe_key("余额不足导致订购失败", "deferred_charge")
    assert k1 == k2
    k3 = compute_dedupe_key("余额不足导致订购失败", "credit_overflow")
    assert k1 != k3   # 同 signature 不同 mechanism -> 不同 key


def test_case_id_differs_by_root_cause():
    """spec Appendix E [case_id 不撞]: 同 dedupe_key 不同 root_cause -> case_id 不同。"""
    dk = compute_dedupe_key("余额不足导致订购失败", "deferred_charge")
    id_a = compute_case_id(dk, "递延收费 时点解耦")
    id_b = compute_case_id(dk, "订购路径无余额闸")
    assert id_a != id_b   # differential / conflict 多根因共存不撞


def test_case_id_same_for_same_inputs():
    """同 dedupe_key + 同 root_cause -> case_id 相同(合并锚, 一致分支用)。"""
    dk = compute_dedupe_key("余额不足导致订购失败", "deferred_charge")
    assert compute_case_id(dk, "递延收费") == compute_case_id(dk, "递延收费")


def test_case_id_is_hex_sha256():
    dk = compute_dedupe_key("x", "y")
    cid = compute_case_id(dk, "z")
    assert len(cid) == 64 and all(c in "0123456789abcdef" for c in cid)
