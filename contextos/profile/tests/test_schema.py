"""Profile schema覆盖 9 namespace + 必填校验。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contextos.profile.schema import (
    InputConfig,
    LLMConfig,
    Profile,
)


def _minimal_dict() -> dict:
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


def test_profile_accepts_minimal_valid_config() -> None:
    p = Profile(**_minimal_dict())
    assert p.llm.provider == "claude"
    assert p.embedding.model == "BAAI/bge-m3"
    assert p.reranker.top_k_output == 10
    assert p.query_expansion.fallback_provider == "local_qwen_2_5_7b"
    assert p.storage.data_dir.endswith("contextos-data")
    assert p.ingestion.min_chunk_chars == 30
    assert p.jdtls_runtime.java_home.endswith("jre21")
    assert p.oracle.dblink_map == {}
    assert p.projects[0].language == "java"


def test_profile_rejects_missing_namespace() -> None:
    d = _minimal_dict()
    del d["oracle"]
    with pytest.raises(ValidationError):
        Profile(**d)


def test_reranker_rejects_zero_top_k() -> None:
    d = _minimal_dict()
    d["reranker"]["top_k_output"] = 0
    with pytest.raises(ValidationError):
        Profile(**d)


def test_projects_must_be_non_empty_list() -> None:
    d = _minimal_dict()
    d["projects"] = []
    with pytest.raises(ValidationError, match="at least 1"):
        Profile(**d)


def test_nested_typo_is_rejected_by_extra_forbid() -> None:
    d = _minimal_dict()
    d["reranker"]["enbled"] = False
    with pytest.raises(ValidationError, match="Extra inputs are not permitted|enbled"):
        Profile(**d)


def test_llm_config_new_fields_default_when_absent() -> None:
    c = LLMConfig(provider="claude", api_key_env="K")
    assert c.base_url is None
    assert c.model is None
    assert c.temperature == 0.0
    assert c.timeout_seconds == 60
    assert c.max_retries == 2


def test_llm_config_temperature_out_of_range_rejected() -> None:
    # 共享 infra 基座:misconfig 在 profile 加载时就拦,不拖到 HTTP 400
    with pytest.raises(ValidationError):
        LLMConfig(provider="claude", api_key_env="K", temperature=2.5)
    with pytest.raises(ValidationError):
        LLMConfig(provider="claude", api_key_env="K", temperature=-0.1)


# --- InputConfig / Profile.input namespace (Plan 02 Task 1) ---

def test_input_config_defaults_when_absent() -> None:
    """不带 [input] 的 profile 仍合法,input 用默认(text + docx 开)。"""
    p = Profile(**_minimal_dict())
    assert p.input.adapters == {"text": True, "docx": True}


def test_input_config_explicit_override() -> None:
    """显式 [input] 覆盖默认。"""
    data = _minimal_dict()
    data["input"] = {"adapters": {"text": True, "docx": False, "email": False}}
    p = Profile(**data)
    assert p.input.adapters["docx"] is False
    assert p.input.adapters["email"] is False


def test_input_config_rejects_unknown_field() -> None:
    """_StrictBase extra=forbid: InputConfig 不接受未知字段。"""
    with pytest.raises(ValidationError):
        InputConfig(adapters={"text": True}, bogus=1)  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]  # 故意造错验 extra=forbid


# --- ScopeConfig (Plan 02b Task 2: guard knobs) ---

def test_scope_config_defaults():
    from contextos.profile.schema import InputConfig
    cfg = InputConfig()
    s = cfg.scope
    assert s.prefilter_enabled is True
    assert s.min_chars == 12
    assert s.min_alpha_ratio == 0.3
    assert s.samples == 1
    assert s.reject_below == 0.5
    assert s.degraded_below == 0.8
    assert s.domain_description == ""
    assert s.signal_terms_path == ""


def test_scope_config_validation_bounds():
    import pytest
    from pydantic import ValidationError
    from contextos.profile.schema import ScopeConfig
    with pytest.raises(ValidationError):
        ScopeConfig(reject_below=1.5)      # > 1.0
    with pytest.raises(ValidationError):
        ScopeConfig(samples=0)             # < 1
    with pytest.raises(ValidationError):
        ScopeConfig(min_alpha_ratio=-0.1)  # < 0


def test_scope_config_extra_forbidden():
    import pytest
    from pydantic import ValidationError
    from contextos.profile.schema import ScopeConfig
    with pytest.raises(ValidationError):
        ScopeConfig(bogus=1)  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]  # 故意造错验 extra=forbid


def test_tables_exclude_table_patterns_default_and_override():
    """方案 B: TablesConfig.exclude_table_patterns(Oracle 正则)默认中立高置信 + 可覆盖 + 空合法。"""
    from contextos.profile.schema import TablesConfig
    d = TablesConfig()
    assert r"_[0-9]{6}$" in d.exclude_table_patterns          # 月表(默认含, 大头)
    assert all("\\d" not in p for p in d.exclude_table_patterns)  # Oracle 语法: 无 \d
    # 可覆盖
    o = TablesConfig(exclude_table_patterns=[r"_X$"])
    assert o.exclude_table_patterns == [r"_X$"]
    # 空列表合法(= 关闭排除)
    assert TablesConfig(exclude_table_patterns=[]).exclude_table_patterns == []


def test_exclude_table_patterns_rejects_empty_pattern():
    """空串/纯空白模式会 NOT REGEXP_LIKE(TABLE_NAME,'') 误排全表(灾难) -> schema 层拒。"""
    from contextos.profile.schema import TablesConfig
    with pytest.raises(ValidationError):
        TablesConfig(exclude_table_patterns=[r"_[0-9]{6}$", ""])
    with pytest.raises(ValidationError):
        TablesConfig(exclude_table_patterns=["   "])


def test_fetch_full_object_metadata_default_false():
    """option A: 默认只抓表级血缘需要的对象元数据(deps+dblinks); columns/indexes/constraints
    是 config 维度的活, 某大型客户代码库满库抓列 ~40min 墙 -> 默认关, 将来 config 按 LP 模板归并抓时 opt-in。"""
    from contextos.profile.schema import TablesConfig
    assert TablesConfig().fetch_full_object_metadata is False
    assert TablesConfig(fetch_full_object_metadata=True).fetch_full_object_metadata is True


def test_exclude_schemas_default_covers_oracle_system_schemas():
    """默认 exclude_schemas 应覆盖 Oracle 标准系统 schema(跨客户中立, 非耦合), 避免逐个白查。"""
    from contextos.profile.schema import TablesConfig
    d = TablesConfig().exclude_schemas
    for s in ["SYS", "SYSTEM", "CTXSYS", "WMSYS", "AUDSYS", "DBSNMP", "OUTLN",
              "APPQOSSYS", "ORDSYS", "OJVMSYS", "GSMADMIN_INTERNAL", "DBSFWUSER",
              "OLAPSYS", "REMOTE_SCHEDULER_AGENT", "LBACSYS", "DVSYS"]:
        assert s in d, f"{s} 应在默认 exclude_schemas"
    assert any("APEX" in p for p in d)        # APEX_* glob
    assert any(p.startswith("C##") for p in d)  # C##* common users glob


def test_scope_config_has_stop_keywords_path():
    from contextos.profile.schema import ScopeConfig
    c = ScopeConfig()
    assert c.stop_keywords_path == ""          # 默认空 = 只用通用 default
    c2 = ScopeConfig(stop_keywords_path="/x/cust.txt")
    assert c2.stop_keywords_path == "/x/cust.txt"
