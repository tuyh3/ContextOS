"""管道内部数据类测试。"""


def test_recovered_sql_candidate_defaults():
    from contextos.lineage.models import RecoveredSqlCandidate
    c = RecoveredSqlCandidate(source_path="a/B.java", line_start=10, line_end=20,
                              container="B.query", sql_text="SELECT 1 FROM DUAL",
                              recovery_mode="literal")
    assert c.confidence == "medium"
    assert c.placeholders == []
    assert c.branch_detected is False


def test_parsed_relation_defaults():
    from contextos.lineage.models import ParsedRelation
    r = ParsedRelation(src_table="A", dst_table="B", relation_type="JOIN")
    assert r.lineage_type == ""
    assert r.is_write_target is False


def test_source_file_and_seq_ref():
    from contextos.lineage.models import SourceFile, SequenceRef
    sf = SourceFile(path="a/B.sql", language="sql", module="a",
                    category="dao_sql", content="SELECT 1")
    assert sf.category == "dao_sql"
    s = SequenceRef(sequence_name="SEQ_X")
    assert s.ref_type == "NEXTVAL"
