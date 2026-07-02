"""W5: config_evidence populate(path C/D 命中证据落库, excerpt 过 sanitize_text)。

设计思路(memory feedback_contextos_test_documentation):
- 四路识别命中(path B DDL COMMENT / path C 业务文档 / path D 客户字典)是 human_confirmed
  loop 的判定依据, 但 C5 build 未把命中证据落 config_evidence 表, 人工 review 时无证据可看。
- 本 task 验证: build 对识别出的 config_table 把命中证据写 config_evidence(evidence_type
  含 rag_business_doc 等), 且 excerpt 必过 sensitive.sanitize_text(敏感值脱敏: RAG excerpt 不
  泄漏敏感值)。
- MED 2: evidence 对 high/confirmed/needs_review 都写(needs_review 最需给人证据), 只 skip 不写。
  本测试 path-C-only 命中 fuse 后落 needs_review(score≈0.4<0.6), 验证 needs_review 也写证据。
- 评分标准(assert):
  1. config_evidence 有一行 evidence_type == 'rag_business_doc'(path C 证据落库)。
  2. 所有 evidence.excerpt 都不含原始敏感值 'supersecret3f7a'(sanitize_text redact)。
- 自动脚本测试逻辑: sqlite in-memory; fake_search 返回含 password= 敏感片段的 hit; build 后
  select config_evidence 断言。
"""
from sqlalchemy import create_engine, select

from contextos.config_dim.schema import metadata, config_evidence
from contextos.config_dim.pipeline import build_config_dimension
from contextos.config_dim.tests.test_pipeline_full import _ProfileStub


def test_config_evidence_written_and_sanitized(tmp_path):
    (tmp_path / "a.properties").write_text("x=1\n", encoding="utf-8")
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)

    class Hit:
        def __init__(s, line):
            s.line = line
            s.rel_path = "activity_document/a.md"

    def fake_search(patterns, subsets):
        # excerpt 含敏感(password=)-> 落库要被 sanitize_text redact
        return [Hit("SYS_CONFIG 配置表 password=supersecret3f7a")] if "business_docs" in subsets else []

    oracle_tables = [{"owner": "UPC", "table": "SYS_CONFIG", "columns": [], "row_count": 1}]
    build_config_dimension(repo_root=tmp_path, profile=_ProfileStub(), engine=eng,
                           cache_dir=tmp_path, oracle_tables=oracle_tables,
                           rag_search=fake_search, db="CTEST")
    with eng.connect() as c:
        evs = list(c.execute(select(config_evidence)))
    assert any(e.evidence_type == "rag_business_doc" for e in evs)  # path C 证据落库
    assert all("supersecret3f7a" not in (e.excerpt or "") for e in evs)  # 敏感 redact
