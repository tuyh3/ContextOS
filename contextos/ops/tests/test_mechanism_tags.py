"""mechanism_tag 受控枚举测试(spec Appendix H.3 MUST)。

设计思路: MECHANISM_TAGS = {tag: behavior_class}(中性种子 tracked)。每个 tag 隶属单一
behavior_class(命名即隐含行为类)。is_known_tag / behavior_class_of 给 schema/synonyms 复用。
评分标准: 5 行为类各至少 1 个 tag;每个 behavior_class 值 ∈ BEHAVIOR_CLASSES;未知 tag 判否。
自动脚本逻辑: 直接断言枚举内容 + MECHANISM_TAGS 值 ∈ BEHAVIOR_CLASSES(同模块, 无跨模块顺序依赖)。
"""
from __future__ import annotations

from contextos.ops.mechanism_tags import (
    BEHAVIOR_CLASSES,
    MECHANISM_TAGS,
    behavior_class_of,
    is_known_tag,
)


def test_seed_tags_present():
    # 至少含设计列举的几个种子 tag
    for tag in ("deferred_charge", "blacklist_default_allow", "config_switch_off",
                "state_stuck", "race_double_submit"):
        assert tag in MECHANISM_TAGS


def test_every_tag_maps_to_valid_behavior_class():
    for tag, bc in MECHANISM_TAGS.items():
        assert bc in BEHAVIOR_CLASSES, f"{tag} -> {bc} 非法 behavior_class"


def test_all_five_behavior_classes_covered():
    covered = set(MECHANISM_TAGS.values())
    assert covered == set(BEHAVIOR_CLASSES), f"未覆盖全 5 类: 缺 {set(BEHAVIOR_CLASSES) - covered}"


def test_helpers():
    assert is_known_tag("deferred_charge") is True
    assert is_known_tag("host_made_up_tag") is False
    assert behavior_class_of("deferred_charge") == "扣费"
    assert behavior_class_of("host_made_up_tag") is None
