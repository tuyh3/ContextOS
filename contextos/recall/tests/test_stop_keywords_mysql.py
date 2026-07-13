"""停用词内核 MySQL builtin 补充测试(spec 2026-07-10 4.5, L3)。

设计思路: MySQL DDL/函数词(AUTO_INCREMENT/DATETIME/IFNULL 等)在每个 MySQL 项目的
建表块与 SQL 里遍地出现, 无特征定位信号, 应进通用停用词内核(与 Oracle 口味的
sql_types_and_builtins 平行, 新开一类别不动既有数组=对 CMPAK 零风险纯增量)。锁两件事:
1. 新增 MySQL builtin 词进入 load_stop_list 全集(大写归一);
2. 既有 Oracle/通用词一个不丢(向后兼容, CMPAK 停用行为不变)。
评分标准: 代表性 MySQL 词全在 + 抽样 Oracle 词仍在; 加载器读所有类别(已验)。
脚本逻辑: 纯加载断言, 无 IO 副作用。
"""
from __future__ import annotations

from contextos.recall.keyword_extract import load_stop_list


def test_mysql_builtins_present() -> None:
    stop = load_stop_list()
    # 大写归一(_flatten_default 统一 upper)
    expected = {
        "AUTO_INCREMENT", "DATETIME", "MEDIUMINT", "TINYINT", "LONGTEXT",
        "IFNULL", "NOW", "CURDATE", "DATE_FORMAT", "CONCAT_WS", "GROUP_CONCAT",
        "UNSIGNED", "ENGINE", "UTF8MB4", "UNIX_TIMESTAMP",
    }
    missing = expected - stop
    assert not missing, f"MySQL builtin 未进停用词: {sorted(missing)}"


def test_existing_keywords_preserved() -> None:
    stop = load_stop_list()
    # 既有 Oracle/通用词一个不丢(向后兼容 CMPAK)
    for kw in ("SYSDATE", "ROWNUM", "VARCHAR2", "NVL", "SELECT", "CREATE_DATE"):
        assert kw in stop, f"既有停用词 {kw} 丢失"
