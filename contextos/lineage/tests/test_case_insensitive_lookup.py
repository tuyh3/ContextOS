"""表身份大小写不敏感匹配测试(spec 2026-07-10 附录 G, L3)。

背景(实测发现, 修正设计 G 前提): NameResolver 两侧都 .upper() 折叠, 故 edges 内部
恒存大写(小写 DDL 的 `ccp_coll_info` 也被 upper 成 CCP_COLL_INFO)。真实缺口不是
"缺 fold 列", 而是查询点(lookup_table/lookup_lineage)的 `== table` 大小写敏感、
入参不 upper —— 小写入参查不到大写 edges。设计 G 的 fold 列+回填迁移对本代码库是
过度设计(内部已统一 upper 折叠), 正确低风险修法 = 查询点 upper 入参跟上内部约定。

锁三件事:
1. 小写建表(mysql) build 后, lookup_table 用小写名能查到(修前 0 命中=缺口);
2. lookup_lineage 上下游同样大小写不敏感;
3. Oracle 向后兼容: 大写入参对大写 edges 行为不变(upper no-op)。
评分标准: 同一张表小写/大写入参命中数必须相等且>0; Oracle 大写路径不回归。
脚本逻辑: 真 build_lineage(小写 DDL + 大写 Java 引用)驱动, 非构造桩。
"""
from __future__ import annotations

from pathlib import Path

from contextos.lineage import store
from contextos.lineage.pipeline import build_lineage
from contextos.lineage.tools import lookup_lineage, lookup_table
from contextos.profile.schema import CodeConfig, TablesConfig
from contextos.storage.db import make_engine


def _build_mysql_lineage(tmp_path: Path):
    # 真实场景: .sql 小写反引号建表+JOIN, Java 大写引用(混用大小写引用同表)
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE `ccp_coll_info` (id INT);\n"
        "SELECT a.x FROM `ccp_coll_info` a "
        "JOIN `ccp_hub_info` b ON a.hid = b.id;\n")
    (tmp_path / "Dao.java").write_text(
        'class Dao { String s = "SELECT * FROM CCP_COLL_INFO WHERE x=1"; }\n')
    eng = make_engine("sqlite://")
    store.create_all(eng)
    store.set_meta(eng, "sql_dialect", "mysql")
    build_lineage(tmp_path, CodeConfig(dao_sql_patterns=[]), TablesConfig(),
                  eng, "2026-07-10", dialect="mysql")
    return eng


class TestCaseInsensitiveLookup:
    def test_lookup_table_lowercase_input_hits(self, tmp_path) -> None:
        eng = _build_mysql_lineage(tmp_path)
        lower = lookup_table(eng, table="ccp_coll_info")
        upper = lookup_table(eng, table="CCP_COLL_INFO")
        # 大小写不敏感: 两种入参命中数相等且 > 0
        assert lower["edges_out"] == upper["edges_out"] > 0
        assert lower["edges_in"] == upper["edges_in"]

    def test_lookup_lineage_case_insensitive(self, tmp_path) -> None:
        eng = _build_mysql_lineage(tmp_path)
        lower = lookup_lineage(eng, table="ccp_hub_info")
        upper = lookup_lineage(eng, table="CCP_HUB_INFO")
        n_lower = len(lower["upstream"]) + len(lower["downstream"])
        n_upper = len(upper["upstream"]) + len(upper["downstream"])
        assert n_lower == n_upper > 0

    def test_oracle_uppercase_unchanged(self, tmp_path) -> None:
        # 向后兼容: Oracle 场景(大写 edges + 大写入参)命中不变
        (tmp_path / "s.sql").write_text(
            "SELECT * FROM T_A a JOIN T_B b ON a.id = b.aid;\n")
        eng = make_engine("sqlite://")
        store.create_all(eng)
        build_lineage(tmp_path, CodeConfig(dao_sql_patterns=[]), TablesConfig(),
                      eng, "2026-07-10")  # 默认 oracle
        r = lookup_table(eng, table="T_A")
        assert r["edges_out"] + r["edges_in"] > 0
