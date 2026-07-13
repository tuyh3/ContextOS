"""profile.code / profile.tables / profile.oracle 扩展字段测试(Plan 05)。"""
from typing import Any

import pytest
from pydantic import ValidationError


def _min_profile_kwargs() -> dict[str, Any]:
    """构造一个最小 Profile(其余 namespace 用既有必填默认)。

    返回类型标 dict[str, Any]: Profile(**kw) 解包 + 嵌套 kw[...] 赋值 pyright-clean
    (review LOW: 否则 dict 字面量推断成窄 union 触发逐参数类型错)。"""
    return dict(
        llm={"provider": "deepseek", "api_key_env": "DEEPSEEK_API_KEY"},
        embedding={"model": "BAAI/bge-m3"},
        reranker={"model": "BAAI/bge-reranker-v2-m3"},
        query_expansion={"translation_provider": "llm", "fallback_provider": "regex"},
        storage={"data_dir": "/tmp/contextos-test"},
        ingestion={},
        jdtls_runtime={"jdtls_path": "/x", "lombok_path": "/y", "java_home": "/z"},
        oracle={"tns_admin": "/tns", "allowed_instances": ["TEST_DB3"]},
        projects=[{"name": "p", "path": "/p", "language": "java"}],
    )


def test_code_config_defaults():
    from contextos.profile.schema import Profile
    p = Profile(**_min_profile_kwargs())
    # code 带默认,向后兼容(既有 profile 不写 [code] 也能加载)
    assert p.code.source_roots == []
    assert "target" in p.code.exclude_dirs
    assert p.code.dao_sql_patterns == []


def test_dao_sql_pattern_shape():
    from contextos.profile.schema import Profile
    kw = _min_profile_kwargs()
    kw["code"] = {
        "dao_sql_patterns": [
            {"path_contains": ["/impl/", "/src/main/"], "conjunction": "all"}
        ]
    }
    p = Profile(**kw)
    assert p.code.dao_sql_patterns[0].conjunction == "all"
    assert p.code.dao_sql_patterns[0].path_contains == ["/impl/", "/src/main/"]
    # conjunction 闭枚举
    kw["code"]["dao_sql_patterns"][0]["conjunction"] = "bogus"
    with pytest.raises(ValidationError):
        Profile(**kw)


def test_tables_config_defaults():
    from contextos.profile.schema import Profile
    p = Profile(**_min_profile_kwargs())
    assert "SYS" in p.tables.exclude_schemas
    assert p.tables.shard_strategy is None       # 默认不归并
    assert p.tables.monthly_pattern == r"_\d{6}$"
    assert p.tables.typo_map == {}


def test_shard_strategy_shape():
    from contextos.profile.schema import Profile
    kw = _min_profile_kwargs()
    kw["tables"] = {"shard_strategy": {"type": "regex", "pattern": r"0?9\d{2}$"},
                    "typo_map": {"CSPF2": "CSFP2"}}
    p = Profile(**kw)
    assert p.tables.shard_strategy is not None        # 收窄 Optional(配置已设)
    assert p.tables.shard_strategy.type == "regex"
    assert p.tables.shard_strategy.pattern == r"0?9\d{2}$"
    assert p.tables.typo_map["CSPF2"] == "CSFP2"


def test_oracle_config_query_limits_defaults():
    from contextos.profile.schema import Profile
    p = Profile(**_min_profile_kwargs())
    # 既有三字段不变 + 新增查询限制带默认(向后兼容)
    assert p.database.oracle.allowed_instances == ["TEST_DB3"]
    assert p.database.oracle.max_rows_hard_limit == 1000
    assert p.database.oracle.query_timeout_seconds == 30
    assert p.database.oracle.reconnect_on_idle is True
    assert p.database.oracle.metadata_cache_ttl_hours == 24
    assert p.database.oracle.instance_alias == {}      # 默认空(裁决 5: 本地表 db 段留空)
