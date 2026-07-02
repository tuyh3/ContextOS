"""Layer 4 .sql 双层恢复测试。"""
from contextos.lineage.models import SourceFile


def _sf(content, category="dao_sql"):
    return SourceFile(path="a/Q.sql", language="sql", module="a",
                      category=category, content=content)


def test_dao_sql_file_medium():
    from contextos.lineage.sql_recover import recover_from_sql_file
    cands = recover_from_sql_file(_sf("SELECT * FROM PM_OFFER_CHA WHERE ID = :id"))
    assert cands
    assert cands[0].recovery_mode == "sql_file"
    assert cands[0].confidence == "medium"


def test_other_sql_low():
    from contextos.lineage.sql_recover import recover_from_sql_file
    cands = recover_from_sql_file(_sf("SELECT 1 FROM DUAL", category="other_sql"))
    assert cands[0].confidence == "low"


def test_multi_statement_split():
    from contextos.lineage.sql_recover import recover_from_sql_file
    sql = "SELECT * FROM A;\nSELECT * FROM B;"
    cands = recover_from_sql_file(_sf(sql))
    assert len(cands) >= 2


def test_sqlplus_noise_fallback():
    """sqlglot 整体 parse 失败时, 按分号切 + 去 REM/PROMPT/SET 噪声。"""
    from contextos.lineage.sql_recover import recover_from_sql_file
    sql = ("REM this is a comment\nPROMPT loading\nSET DEFINE OFF\n"
           "SELECT * FROM T_USER WHERE NAME LIKE '%' || :x || '%';\n@@other.sql\n")
    cands = recover_from_sql_file(_sf(sql, category="other_sql"))
    assert any("T_USER" in c.sql_text for c in cands)


def test_empty_returns_nothing():
    from contextos.lineage.sql_recover import recover_from_sql_file
    assert recover_from_sql_file(_sf("   ")) == []
