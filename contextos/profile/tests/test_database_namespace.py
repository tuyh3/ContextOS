"""profile [database] 统一段 + [oracle] 兼容垫片测试(spec 2026-07-10 附录 A)。

设计思路: [database] 带 type 判别式是多方言的最上游契约, 本文件锁五件事:
1. 垫片语义(A.2): 存量 [oracle] 写法零破坏加载, 归一进 database(type=oracle);
   归一后 profile.oracle 清为 None——机械强制 A.3 的"直接引用清零"(任何残留
   引用会在真跑时炸出来, 不靠 convention);
2. 判别式约束: type 与子段一一对应, 缺/错/多子段都是配置错误硬拒;
3. 两段并存([oracle]+[database])硬拒(A.2);
4. postgres/opengauss 预留未实装: 显式报错拒载, 不静默降级(A.1);
5. validator 扩展(F.2 双执行点): mysql 实例三串过 prod 关键词闸,
   tns_admin 路径检查只对 oracle 生效, mysql alias 须可拼环境变量名(A.5)。
评分标准: 拒绝路径逐一独立用例 + 报错信息可定位; 加载路径断言归一后形状。
脚本逻辑: 纯单元测试, 复用 conftest 中性值范式, 不掺真客户标识。
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from contextos.profile.schema import Profile
from contextos.profile.validator import ProfileValidationError, validate_profile


def _base_dict(**overrides: Any) -> dict:
    """9 namespace 中性最小 dict(照抄 conftest 范式), 按 kwargs 覆写。"""
    d: dict[str, Any] = {
        "llm": {"provider": "test_llm", "api_key_env": "PROFILE_TEST_LLM_KEY"},
        "embedding": {"model": "test-embed"},
        "reranker": {"enabled": True, "model": "test-rerank",
                     "top_k_input": 50, "top_k_output": 10},
        "query_expansion": {"enabled": True, "translation_provider": "main_llm",
                            "fallback_provider": "local"},
        "storage": {"data_dir": "/tmp/ctx-test-data"},
        "ingestion": {"default_cleanup": "full",
                      "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "/jdtls",
                          "lombok_path": "/jdtls/lombok.jar", "java_home": "/jre"},
        "oracle": {"tns_admin": "/tns", "allowed_instances": ["TEST_DB1"]},
        "projects": [{"name": "proj", "path": "/proj",
                      "language": "java", "build_system": "gradle"}],
    }
    d.update(overrides)
    return d


_MYSQL_DB = {
    "type": "mysql",
    "mysql": {"instances": [{
        "alias": "test_inst", "host": "127.0.0.1", "port": 3306,
        "databases": ["appdb", "appdb_ext"],
    }]},
}


class TestOracleShim:
    def test_legacy_oracle_section_still_loads(self) -> None:
        p = Profile.model_validate(_base_dict())
        assert p.database is not None
        assert p.database.type == "oracle"
        assert p.database.oracle is not None
        assert p.database.oracle.allowed_instances == ["TEST_DB1"]

    def test_shim_clears_top_level_oracle_field(self) -> None:
        # A.3 机械强制: 归一后顶层 oracle=None, 残留 profile.oracle.* 引用必炸
        p = Profile.model_validate(_base_dict())
        assert p.oracle is None

    def test_explicit_database_oracle_form_loads(self) -> None:
        d = _base_dict()
        ora = d.pop("oracle")
        d["database"] = {"type": "oracle", "oracle": ora}
        p = Profile.model_validate(d)
        assert p.database.type == "oracle"
        assert p.database.oracle.tns_admin == "/tns"

    def test_both_sections_hard_rejected(self) -> None:
        d = _base_dict()
        d["database"] = {"type": "oracle", "oracle": dict(d["oracle"])}
        with pytest.raises(ValidationError, match="both"):
            Profile.model_validate(d)

    def test_neither_section_rejected(self) -> None:
        d = _base_dict()
        d.pop("oracle")
        with pytest.raises(ValidationError, match="database"):
            Profile.model_validate(d)


class TestTypeDiscriminator:
    def test_mysql_form_loads(self) -> None:
        d = _base_dict()
        d.pop("oracle")
        d["database"] = dict(_MYSQL_DB)
        p = Profile.model_validate(d)
        assert p.database.type == "mysql"
        inst = p.database.mysql.instances[0]
        assert inst.alias == "test_inst"
        assert inst.databases == ["appdb", "appdb_ext"]
        assert inst.port == 3306

    def test_type_without_matching_subsection_rejected(self) -> None:
        d = _base_dict()
        d.pop("oracle")
        d["database"] = {"type": "mysql"}
        with pytest.raises(ValidationError, match="requires"):
            Profile.model_validate(d)

    def test_mismatched_subsection_rejected(self) -> None:
        d = _base_dict()
        ora = d.pop("oracle")
        d["database"] = {"type": "mysql", "oracle": ora,
                         "mysql": dict(_MYSQL_DB["mysql"])}
        with pytest.raises(ValidationError, match="does not match"):
            Profile.model_validate(d)

    @pytest.mark.parametrize("reserved", ["postgres", "opengauss"])
    def test_reserved_types_rejected_explicitly(self, reserved: str) -> None:
        d = _base_dict()
        d.pop("oracle")
        d["database"] = {"type": reserved}
        with pytest.raises(ValidationError, match="(?i)reserved|未实装"):
            Profile.model_validate(d)

    def test_mysql_alias_must_fit_env_var(self) -> None:
        # A.5: 凭据走 MYSQL_<ALIAS>_USER, alias 必须能拼合法环境变量名
        d = _base_dict()
        d.pop("oracle")
        bad = {"type": "mysql", "mysql": {"instances": [{
            "alias": "bad-name!", "host": "127.0.0.1", "databases": ["appdb"],
        }]}}
        d["database"] = bad
        with pytest.raises(ValidationError, match="alias"):
            Profile.model_validate(d)


class TestValidatorExtension:
    def test_mysql_prod_keyword_in_host_rejected(self) -> None:
        d = _base_dict()
        d.pop("oracle")
        d["database"] = {"type": "mysql", "mysql": {"instances": [{
            "alias": "neutral", "host": "db-master.internal",
            "databases": ["appdb"],
        }]}}
        p = Profile.model_validate(d)
        with pytest.raises(ProfileValidationError, match="production keyword"):
            validate_profile(p, check_paths=False)

    def test_oracle_prod_keyword_still_rejected_via_shim(self) -> None:
        d = _base_dict(oracle={"tns_admin": "/tns",
                               "allowed_instances": ["PROD_PAM"]})
        p = Profile.model_validate(d)
        with pytest.raises(ProfileValidationError, match="production keyword"):
            validate_profile(p, check_paths=False)

    def test_mysql_profile_skips_tns_admin_path_check(self, tmp_path) -> None:
        # tns_admin 是 Oracle 概念, mysql profile 不该被它的路径检查拦(A 契约)
        d = _base_dict()
        d.pop("oracle")
        d["database"] = dict(_MYSQL_DB)
        d["jdtls_runtime"] = {"jdtls_path": str(tmp_path),
                              "lombok_path": str(tmp_path), "java_home": str(tmp_path)}
        d["projects"] = [{"name": "proj", "path": str(tmp_path),
                          "language": "java", "build_system": "maven"}]
        p = Profile.model_validate(d)
        validate_profile(p, check_paths=True)   # 不应 raise


class TestAliasUniqueness:
    """冷验证 M1(2026-07-10): alias 是凭据键(MYSQL_<ALIAS>_USER), 重复=凭据静默碰撞。
    env 变量名 upper 化拼接, 故按 case-insensitive 查重。"""

    def test_duplicate_alias_rejected(self) -> None:
        d = _base_dict()
        d.pop("oracle")
        d["database"] = {"type": "mysql", "mysql": {"instances": [
            {"alias": "same_inst", "host": "10.0.0.1", "databases": ["a"]},
            {"alias": "same_inst", "host": "10.0.0.2", "databases": ["b"]},
        ]}}
        with pytest.raises(ValidationError, match="duplicate"):
            Profile.model_validate(d)

    def test_case_insensitive_duplicate_rejected(self) -> None:
        d = _base_dict()
        d.pop("oracle")
        d["database"] = {"type": "mysql", "mysql": {"instances": [
            {"alias": "Inst_A", "host": "10.0.0.1", "databases": ["a"]},
            {"alias": "inst_a", "host": "10.0.0.2", "databases": ["b"]},
        ]}}
        with pytest.raises(ValidationError, match="duplicate"):
            Profile.model_validate(d)
