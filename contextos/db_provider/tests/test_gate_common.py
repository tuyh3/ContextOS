"""gate_common 中性安全闸门测试(spec 2026-07-10 附录 F, 硬约束 #4 MySQL 口径)。

设计思路: 反扣测试为主(绕过尝试必须 raise), 对齐 test_oracle_gate.py 传统。
锁四件事:
1. PRODUCTION_KEYWORDS 单一 SSOT——oracle_gate 与 gate_common 是同一个对象
   (F.3: 上提后 oracle_gate 反向 import, 不允许两份词表漂移);
2. assert_instance_is_test_only 三串全扫(alias + host + 每个库名), 任一含
   PROD/PRD/LIVE/MASTER/RELEASE 即拒——alias 是用户自起的可规避, 所以 host/库名
   必须一起扫(F.1 的别名漏洞对策);
3. 白名单精确匹配 + fail-closed(空白名单/异常态一律拒);
4. DbSafetyError 是统一异常基类, OracleSafetyError 是其子类(存量 except 不破)。
评分标准: 每个拒绝路径都有独立用例; 通过路径仅白名单内全中性串一条。
脚本逻辑: 纯单元测试, 零 IO。
"""
from __future__ import annotations

import pytest

from contextos.db_provider.gate_common import (
    PRODUCTION_KEYWORDS,
    DbSafetyError,
    assert_instance_is_test_only,
)


class TestKeywordsSingleSSOT:
    def test_oracle_gate_reimports_same_tuple(self) -> None:
        from contextos.db_provider import oracle_gate
        assert oracle_gate.PRODUCTION_KEYWORDS is PRODUCTION_KEYWORDS

    def test_keyword_values_locked(self) -> None:
        assert PRODUCTION_KEYWORDS == ("PROD", "PRD", "LIVE", "MASTER", "RELEASE")

    def test_oracle_safety_error_is_subclass(self) -> None:
        from contextos.db_provider.oracle_gate import OracleSafetyError
        assert issubclass(OracleSafetyError, DbSafetyError)


class TestInstanceGatePasses:
    def test_clean_instance_in_whitelist_passes(self) -> None:
        assert_instance_is_test_only(
            alias="bomc_test", host="127.0.0.1",
            databases=["toptea", "sysm_xx"], allowed_aliases=["bomc_test"],
        )


class TestInstanceGateRejects:
    @pytest.mark.parametrize("alias", [
        "prod_db", "my_PRD", "live_x", "master1", "release_a",
    ])
    def test_keyword_in_alias_rejected(self, alias: str) -> None:
        with pytest.raises(DbSafetyError, match="production keyword"):
            assert_instance_is_test_only(
                alias=alias, host="127.0.0.1",
                databases=["toptea"], allowed_aliases=[alias],
            )

    @pytest.mark.parametrize("host", [
        "prod-mysql.internal", "10.1.1.1.PRD.corp", "db-master-replica",
    ])
    def test_keyword_in_host_rejected_even_with_neutral_alias(self, host: str) -> None:
        # 别名漏洞对策: alias 中性也拦(用户自起别名绕不过 host 扫描)
        with pytest.raises(DbSafetyError, match="production keyword"):
            assert_instance_is_test_only(
                alias="neutral", host=host,
                databases=["toptea"], allowed_aliases=["neutral"],
            )

    @pytest.mark.parametrize("db", ["toptea_prod", "LIVE_bill", "release"])
    def test_keyword_in_any_database_rejected(self, db: str) -> None:
        with pytest.raises(DbSafetyError, match="production keyword"):
            assert_instance_is_test_only(
                alias="neutral", host="127.0.0.1",
                databases=["toptea", db], allowed_aliases=["neutral"],
            )

    def test_alias_not_in_whitelist_rejected(self) -> None:
        with pytest.raises(DbSafetyError, match="not in allowed"):
            assert_instance_is_test_only(
                alias="other", host="127.0.0.1",
                databases=["toptea"], allowed_aliases=["bomc_test"],
            )

    def test_empty_whitelist_fail_closed(self) -> None:
        with pytest.raises(DbSafetyError):
            assert_instance_is_test_only(
                alias="bomc_test", host="127.0.0.1",
                databases=["toptea"], allowed_aliases=[],
            )

    def test_empty_databases_fail_closed(self) -> None:
        # 零库配置是配置错误, 不放行
        with pytest.raises(DbSafetyError, match="databases"):
            assert_instance_is_test_only(
                alias="bomc_test", host="127.0.0.1",
                databases=[], allowed_aliases=["bomc_test"],
            )
