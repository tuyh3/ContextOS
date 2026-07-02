"""record_confirmed_case 入参模型测试(spec Appendix A 签名 + 枚举)。

设计思路: RecordCaseInput(_StrictBase) 校验 behavior_class/source_type/confirmed_by_role/
relation 枚举 + 必填非空。confirmed_by_actor_id 不是入参(服务端注入)。
评分标准: 合法入参构造成功;枚举越界 / 必填空 raise;extra 字段 forbid;relation 可空。
自动脚本逻辑: pydantic ValidationError 断言。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contextos.ops.case_schema import RecordCaseInput


_VALID = dict(
    phenomenon_signature="信用额度内订购大额套餐成功",
    search_terms="递延收费 余额不足",
    behavior_class="扣费",
    confirmed_root_cause="递延收费 时点解耦",
    mechanism_tag="deferred_charge",
    evidence_pointers=["fqn:com.example.Foo.bar"],
    decisive_data_note=None,
    confirmed_by_role="expert",
    source_type="manual",
    source_ref=None,
    relation=None,
)


def test_valid_input():
    m = RecordCaseInput(**_VALID)
    assert m.behavior_class == "扣费"
    assert m.relation is None


def test_bad_behavior_class_rejected():
    bad = dict(_VALID, behavior_class="不存在的类")
    with pytest.raises(ValidationError):
        RecordCaseInput(**bad)


def test_bad_relation_rejected():
    bad = dict(_VALID, relation="merge")
    with pytest.raises(ValidationError):
        RecordCaseInput(**bad)


def test_empty_required_rejected():
    bad = dict(_VALID, phenomenon_signature="")
    with pytest.raises(ValidationError):
        RecordCaseInput(**bad)


def test_actor_id_not_an_input_field():
    """confirmed_by_actor_id 服务端注入, 不是 host 参数 -> extra forbid 拒。"""
    bad = dict(_VALID, confirmed_by_actor_id="someone")
    with pytest.raises(ValidationError):
        RecordCaseInput(**bad)


def test_unknown_mechanism_tag_rejected():
    """spec Appendix H.3 MUST: 未知 mechanism_tag fail-closed reject(防 host 造 tag 污染)。"""
    bad = dict(_VALID, mechanism_tag="host_made_up_tag")
    with pytest.raises(ValidationError):
        RecordCaseInput(**bad)


def test_behavior_class_mismatch_rejected():
    """spec Appendix H.3 MUST: MECHANISM_TAGS[tag] != behavior_class -> reject。
    deferred_charge 隶属 扣费; 配它 资格 应拒。"""
    bad = dict(_VALID, mechanism_tag="deferred_charge", behavior_class="资格")
    with pytest.raises(ValidationError):
        RecordCaseInput(**bad)
