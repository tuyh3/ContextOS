"""record_confirmed_case 入参模型 + 枚举常量(spec Appendix A)。

RecordCaseInput: host 传的入参(pydantic _StrictBase extra=forbid)。
  confirmed_by_actor_id **不在此** —— 服务端从认证上下文注入(防伪造, spec Appendix B)。
枚举: SOURCE_TYPES / CONFIRMED_BY_ROLES / RELATIONS。BEHAVIOR_CLASSES 从 mechanism_tags 导入复用
(最底层常量源, 见 mechanism_tags.py 注释), 此处 import 后供 RecordCaseInput 与下游引用。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

# BEHAVIOR_CLASSES 与 MECHANISM_TAGS 同源自 mechanism_tags(最底层常量源, 避免测试期顺序耦合)。
from contextos.ops.mechanism_tags import BEHAVIOR_CLASSES, MECHANISM_TAGS
# 复用项目约定的严格基类(extra=forbid), 不裸 BaseModel(对齐 profile/schema.py 全 schema)。
from contextos.profile.schema import _StrictBase

# mechanism_tag 命名隐含其行为类(Appendix H.3); _BehaviorClass Literal 与 BEHAVIOR_CLASSES 同值。
SOURCE_TYPES = ("manual", "incident", "ticket", "expert_review")
CONFIRMED_BY_ROLES = ("expert", "ops", "dev", "support")
RELATIONS = ("differential", "conflict")

_BehaviorClass = Literal["扣费", "资格", "配置", "数据状态", "时序"]
_SourceType = Literal["manual", "incident", "ticket", "expert_review"]
_Role = Literal["expert", "ops", "dev", "support"]
_Relation = Literal["differential", "conflict"]


class RecordCaseInput(_StrictBase):
    # _StrictBase 已锁 extra=forbid(actor_id 等非入参字段会被拒)。

    phenomenon_signature: str = Field(min_length=1)
    search_terms: str = Field(min_length=1)
    behavior_class: _BehaviorClass
    confirmed_root_cause: str = Field(min_length=1)
    mechanism_tag: str = Field(min_length=1)
    evidence_pointers: list[str] = Field(default_factory=list)
    decisive_data_note: str | None = None
    confirmed_by_role: _Role
    source_type: _SourceType
    source_ref: str | None = None
    relation: _Relation | None = None

    @field_validator("mechanism_tag")
    @classmethod
    def _no_sep(cls, v: str) -> str:
        # mechanism_tag 进 dedupe_key 拼接(\x1f 分隔), 自身不得含分隔符 / 空白
        if "\x1f" in v or any(c.isspace() for c in v):
            raise ValueError("mechanism_tag 不得含空白 / 分隔符")
        return v

    @model_validator(mode="after")
    def _mechanism_tag_controlled(self) -> "RecordCaseInput":
        # spec Appendix H.3 MUST: mechanism_tag 受控枚举, 未知 fail-closed(防 host 造 tag
        # 污染 dedupe/synonym pool); 且 MECHANISM_TAGS[tag] 必须 == 提交的 behavior_class
        # (命名即隐含行为类, 单键已足够区分)。
        bc = MECHANISM_TAGS.get(self.mechanism_tag)
        if bc is None:
            raise ValueError(
                f"mechanism_tag {self.mechanism_tag!r} 不在受控枚举 MECHANISM_TAGS; "
                "新机制族扩展需人工加种子(受控), 非 host 自造"
            )
        if bc != self.behavior_class:
            raise ValueError(
                f"mechanism_tag {self.mechanism_tag!r} 隶属 behavior_class {bc!r}, "
                f"与提交的 {self.behavior_class!r} 不一致(reject)"
            )
        return self
