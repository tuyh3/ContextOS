"""Plan 06 Task A1: profile 扩 config_tables / config 命名空间(通用中立默认)。

设计思路
--------
06 配置维度桥需要两个新 profile 命名空间(对齐 design 06 §3.4/§3.5):
- `config_tables`: DB 配置表识别(路径 A 启发式)的旋钮 — 表名启发 / 规则列 /
  注释关键词(中英) + big_table 行阈值。
- `config`: 配置**文件**源(.properties/.yaml/.json/.xml)+ 自研框架注解名
  (C+B 策略)+ 敏感 key patterns(sanitizer chokepoint 的词表来源)。

评分标准(本测试守护的契约)
--------------------------
1. 默认值"通用中立":不带客户业务词(红线 #3 — 字典自动抽取,不手工梳理;
   default 跨域中立,业务词由客户/seed 填)。`name_patterns` 默认空(客户填),
   `rule_columns` 是跨域通用列名,`sensitive_key_patterns` 含通用 "password"。
2. 文件源默认开关非空(.properties/.yaml/... 起步),`framework_annotations` 默认空。
3. `big_table_row_threshold` == 50000(design §3.4 大表分级阈值)。
4. 两个命名空间沿用 `_StrictBase`(extra='forbid'):传未知字段抛 ValidationError。

自动脚本测试逻辑
----------------
- test_config_namespaces_default_empty_and_neutral: 构造最小合法 Profile(其余必填
  namespace 用既有默认 fixture),只断言**新增** config_tables/config 的默认形状与中立性。
- test_config_extra_forbidden: 在合法 Profile 的 config 段注入未知字段,核验
  ConfigConfig 的 extra='forbid' 真的拒绝(而非因缺其他必填字段误绿)。

偏离 plan 蓝本
--------------
plan Step 1 写 `p = Profile()`(全默认)。但既有 schema 的 llm/embedding/oracle/
projects 等是必填(无默认),`Profile()` 无参会先在那些字段上 ValidationError,与本
task 无关。故沿用 test_schema.py 的 `_minimal_dict()` 模式构造合法 Profile,再断言
新命名空间默认 — 测试意图(验证 config_tables/config 中立默认)不变。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contextos.profile.schema import Profile


def _minimal_dict() -> dict:
    """最小合法 Profile(其余 namespace 用既有必填默认), 对齐 test_schema.py。"""
    return {
        "llm": {"provider": "claude", "api_key_env": "ANTHROPIC_API_KEY"},
        "embedding": {"model": "BAAI/bge-m3", "device": "cpu"},
        "reranker": {"enabled": True, "model": "BAAI/bge-reranker-v2-m3",
                     "top_k_input": 50, "top_k_output": 10},
        "query_expansion": {"enabled": True, "translation_provider": "main_llm",
                            "fallback_provider": "local_qwen_2_5_7b"},
        "storage": {"data_dir": "/tmp/contextos-data"},
        "ingestion": {"default_cleanup": "full", "chunk_strategy": "h2_h3",
                      "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "/opt/jdtls/server",
                          "lombok_path": "/opt/jdtls/lombok.jar",
                          "java_home": "/opt/jre21"},
        "oracle": {"tns_admin": "/etc/tns",
                   "allowed_instances": ["TEST_DB1"]},
        "projects": [{"name": "demoproj", "path": "/data/demoproj",
                      "language": "java", "build_system": "gradle"}],
    }


def test_config_namespaces_default_empty_and_neutral() -> None:
    p = Profile(**_minimal_dict())  # 新增命名空间走默认
    # config_tables 检测默认值: name_patterns/rule_columns 通用中立, 不带客户业务词
    det = p.config_tables.detection
    assert isinstance(det.name_patterns, list)
    assert det.name_patterns == []                # 表名启发默认空(客户/seed 填)
    assert isinstance(det.rule_columns, list)
    assert det.rule_columns                        # 跨域通用规则列, 非空
    assert "STATUS" in det.rule_columns            # 通用列(非客户业务)
    assert p.config_tables.big_table_row_threshold == 50000
    # config 文件源默认开关
    assert p.config.file_sources.include_extensions  # 非空默认 (.properties/.yaml/...)
    assert ".properties" in p.config.file_sources.include_extensions
    assert isinstance(p.config.framework_annotations, list)
    assert p.config.framework_annotations == []    # 自研框架注解默认空(profile 驱动)
    # 敏感 key patterns 默认含通用项
    assert any("password" in x for x in p.config.sensitive_key_patterns)


def test_config_extra_forbidden() -> None:
    d = _minimal_dict()
    d["config"] = {"bogus_field": 1}               # 合法其余字段 + config 段注入未知字段
    with pytest.raises(ValidationError):
        Profile(**d)
