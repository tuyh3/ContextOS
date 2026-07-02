"""02 需求拆解输出契约。对齐 docs/需求与设计/v1/02-需求拆解/design.md §2。

枚举内联(模型小,不另建 enums.py)。dict_hits 在 v1 deferred(留接缝,pipeline 不填充)。
所有 LLM 可能漏产的字段给默认值;只有 pipeline 一定能产的 3 字段必填。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class _StrictBase(BaseModel):
    model_config = {"extra": "forbid"}


# --- 枚举(Literal SSOT)---

# §1.1 输入来源(v1 实装 text + docx + email,im/transcript v2 占位)
SourceKind = Literal["docx", "email", "im", "transcript", "text"]

# §3.2 8 类业务能力
Capability = Literal[
    "product-subscription",   # 产品订购
    "billing-charging",       # 计费/账务
    "eligibility-check",      # 资格校验
    "ussd-menu",              # USSD 菜单
    "admin-config",           # 管理后台配置
    "esb-interface",          # ESB / 能开接口
    "batch-job",              # 批处理 / ET 进程
    "notification",           # 通知 / SMS / Email
]

# §2 actions 动作分类(影响 01 change_type)
ActionKind = Literal["add", "modify", "delete"]

# §2 候选种子的 kind(各维不同)
CodeNameKind = Literal["shouty", "camelcase", "proper_noun", "other"]
TableTermKind = Literal["entity", "table_hint", "business_term"]
ConfigKeyKind = Literal["config_key", "param_term", "config_table_hint"]

# 候选来源:v1 产出 regex / llm;dict-* 是 dict 桥(Plan 04/05 后)的占位
CandidateSource = Literal[
    "regex", "llm", "dict-capability", "dict-config", "dict-config-table"
]


# --- 嵌套模型 ---

class MatchedCapability(_StrictBase):
    capability: Capability
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence: str = ""


class CandidateName(_StrictBase):
    """给 04 代码搜索当 workspaceSymbol 种子。"""
    term: str
    kind: CodeNameKind
    source: CandidateSource
    source_span: str = ""     # 该候选在需求原文的出处片段; Guard 2 grounding 核验依据
    segment_path: list[str] = Field(default_factory=list)  # 候选来自哪段(代码填; 评测/调试)


class CandidateTableTerm(_StrictBase):
    """给 05 数据库血缘当种子(业务实体词/表名前缀)。"""
    term: str
    kind: TableTermKind
    source: CandidateSource
    source_span: str = ""     # grounding 核验依据
    segment_path: list[str] = Field(default_factory=list)  # 候选来自哪段(代码填; 评测/调试)


class CandidateConfigKey(_StrictBase):
    """给 06 配置维度当种子(配置 key/参数词)。"""
    term: str
    kind: ConfigKeyKind
    source: CandidateSource
    source_span: str = ""     # grounding 核验依据
    segment_path: list[str] = Field(default_factory=list)  # 候选来自哪段(代码填; 评测/调试)


class InterfaceDictHit(_StrictBase):
    """字典桥命中(v1 deferred,留 schema)。"""
    capability: str
    service: str
    source: str


class DictHits(_StrictBase):
    """桥3 字典输入。v1 全空(dict 桥 defer 到 Plan 04/05 之后)。"""
    interface_dict: list[InterfaceDictHit] = Field(default_factory=list)
    capability_line: list[dict] = Field(default_factory=list)   # v2 shape TBD
    ussd_menu: list[dict] = Field(default_factory=list)         # v2
    admin_menu: list[dict] = Field(default_factory=list)        # v2


class Queries(_StrictBase):
    """给 03 双路检索的双语 query。"""
    zh: str = ""
    en: str = ""


# --- 顶层 ---

class RequirementBreakdown(_StrictBase):
    """02 模块输出。各下游 provider 各取所需(见 design §2 末"为什么结构化")。"""
    requirement_id: str
    raw_text: str
    source_kind: SourceKind

    # --- Plan 02b 三道 guard 产出(均带默认, 向后兼容)---
    assessment: Literal["ok", "degraded", "rejected"] = "ok"
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0

    business_intent: str = ""
    key_entities: list[str] = Field(default_factory=list)
    actions: list[ActionKind] = Field(default_factory=list)

    matched_capabilities: list[MatchedCapability] = Field(default_factory=list)
    candidate_code_names: list[CandidateName] = Field(default_factory=list)
    candidate_table_terms: list[CandidateTableTerm] = Field(default_factory=list)
    candidate_config_keys: list[CandidateConfigKey] = Field(default_factory=list)

    dict_hits: DictHits = Field(default_factory=DictHits)
    queries: Queries = Field(default_factory=Queries)
    open_questions: list[str] = Field(default_factory=list)
