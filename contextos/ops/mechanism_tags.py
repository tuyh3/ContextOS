"""mechanism_tag 受控枚举(spec Appendix H.3 MUST: 受控、隶属单一 behavior_class、防污染)。

为什么受控: dedupe_key / case_id / 同义池归并锚都用 mechanism_tag 单键。若 host 可造任意 tag,
不可信 host 能用伪 tag 污染 dedupe(造永不命中的 key)或 synonym pool(乱并同义词)。故 tag
必须 ∈ MECHANISM_TAGS 且其 behavior_class 与提交的 behavior_class 一致, 否则 record reject;
未知 tag fail-closed、不自动新建。新机制族扩展 = 人工加种子(受控), 非 host 自造。
命名规范: 每 tag 隶属单一 behavior_class(命名即隐含行为类、不跨类同名)-> 单键已足够区分。

中性种子(无客户名): 5 行为类各 1-2 个常见机制族。

BEHAVIOR_CLASSES 定义在本模块(最底层常量源), case_schema 等上层 import 此处; 不放 case_schema
避免 case_schema<->mechanism_tags 的测试期 import 顺序耦合(test_mechanism_tags 在 Step 5.0 即跑)。
"""
from __future__ import annotations

# 5 行为类(spec §2 路由)。最底层常量源, 上层(case_schema)import 复用。
BEHAVIOR_CLASSES = ("扣费", "资格", "配置", "数据状态", "时序")

# tag -> behavior_class(每个值必须 ∈ BEHAVIOR_CLASSES)。
MECHANISM_TAGS: dict[str, str] = {
    # 扣费
    "deferred_charge": "扣费",
    "credit_decoupled": "扣费",
    "order_no_balance_gate": "扣费",
    # 资格
    "blacklist_default_allow": "资格",
    "whitelist_unconfigured": "资格",
    # 配置
    "config_switch_off": "配置",
    # 数据状态
    "state_stuck": "数据状态",
    # 时序
    "race_double_submit": "时序",
}


def is_known_tag(tag: str) -> bool:
    return tag in MECHANISM_TAGS


def behavior_class_of(tag: str) -> str | None:
    """返回 tag 的 behavior_class; 未知 tag 返回 None(调用方 fail-closed)。"""
    return MECHANISM_TAGS.get(tag)
