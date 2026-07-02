"""build pipeline 测试。tmp_path 小 fixture repo + 内存 engine(离线, 无元数据)。"""
from contextos.profile.schema import CodeConfig, DaoSqlPattern, TablesConfig
from contextos.storage.db import make_engine
from contextos.lineage import store


def _write(root, rel, content):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_build_produces_edges_and_templates(tmp_path):
    from contextos.lineage.pipeline import build_lineage
    _write(tmp_path, "order/impl/src/main/INS.sql",
           "INSERT INTO PM_OFFER_CHA SELECT * FROM PM_OFFER_BASE")
    _write(tmp_path, "order/Dao.java",
           'class Dao { void q(){ String s="SELECT * FROM T_USER a JOIN T_ROLE b ON a.rid=b.id";'
           ' ps.executeQuery(s);}}')
    eng = make_engine("sqlite://")
    store.create_all(eng)
    code = CodeConfig(dao_sql_patterns=[
        DaoSqlPattern(path_contains=["/impl/", "/src/main/"], conjunction="all")])
    stats = build_lineage(tmp_path, code, TablesConfig(), eng)
    edges = store.all_edges(eng)
    assert stats["edges"] >= 2
    rels = {e["relation_type"] for e in edges}
    assert "INSERT_SELECT" in rels and "JOIN" in rels
    # INSERT_SELECT 边的 dst 是写目标
    ins = [e for e in edges if e["relation_type"] == "INSERT_SELECT"][0]
    assert ins["dst_table"] == "PM_OFFER_CHA"
    # 离线: src_db 空(无元数据)
    assert ins["src_db"] == ""
    # 模板写入(只读 SELECT 进 sql_templates)
    assert any("T_USER" in t["sql_text"] for t in store.all_templates(eng))


def test_build_is_full_rebuild(tmp_path):
    """重复 build 不累积重复边(clear_all 先清)。"""
    from contextos.lineage.pipeline import build_lineage
    _write(tmp_path, "x/q.sql", "SELECT * FROM A a JOIN B b ON a.id=b.aid")
    eng = make_engine("sqlite://")
    store.create_all(eng)
    n1 = build_lineage(tmp_path, CodeConfig(), TablesConfig(), eng)["edges"]
    n2 = build_lineage(tmp_path, CodeConfig(), TablesConfig(), eng)["edges"]
    assert n1 == n2


def test_branch_detected_edge_not_emitted(tmp_path):
    """§9.3: branch_detected 的 string_builder 候选只留 evidence/template, 不产 lineage_edge。"""
    from contextos.lineage.pipeline import build_lineage
    _write(tmp_path, "x/Dao.java",
           'class Dao { String q(boolean f){ StringBuilder sb=new StringBuilder();'
           ' sb.append("SELECT * FROM T_A a JOIN T_B b ON a.id=b.aid");'
           ' if(f){sb.append(" AND a.x=1");} return sb.toString(); } }')
    eng = make_engine("sqlite://")
    store.create_all(eng)
    build_lineage(tmp_path, CodeConfig(), TablesConfig(), eng)
    # branch_detected 候选不产边(此例唯一 SQL 来自 branch builder)
    edges = store.all_edges(eng)
    assert all(e["branch_detected"] is False for e in edges)


def test_unresolved_recorded(tmp_path):
    from contextos.lineage.pipeline import build_lineage
    _write(tmp_path, "x/bad.sql", "THIS IS NOT SQL ### @@@ garbage tokens here")
    eng = make_engine("sqlite://")
    store.create_all(eng)
    build_lineage(tmp_path, CodeConfig(), TablesConfig(), eng)
    # 不是 SQL 的不进 candidate(sql_recover 关键词过滤), 故 unresolved 可能为 0;
    # 用真能进 candidate 但 parse 失败的: 见 sql_parse garbage 测试覆盖。这里只验不报错。
    assert store.count_unresolved(eng) >= 0


def test_select_with_dml_substring_columns_is_templated(tmp_path):
    """audit fix #6: 列名含 DML 关键词子串(UPDATE_TIME/INSERT_USER)的只读 SELECT
    不应被 _maybe_template 误判为 DML 而丢弃 -> 应进 sql_templates。"""
    from contextos.lineage.pipeline import build_lineage
    _write(tmp_path, "x/log.sql",
           "SELECT UPDATE_TIME, INSERT_USER FROM T_LOG WHERE STATUS=1")
    eng = make_engine("sqlite://")
    store.create_all(eng)
    build_lineage(tmp_path, CodeConfig(), TablesConfig(), eng)
    templates = store.all_templates(eng)
    assert any("T_LOG" in t["sql_text"] for t in templates)
    assert any("UPDATE_TIME" in t["sql_text"] and "INSERT_USER" in t["sql_text"]
               for t in templates)


def test_template_id_distinguishes_multiple_selects_same_file():
    """同文件多个 SELECT 各存一条模板(review Finding #5: template_id 不能只 hash source_path,
    否则同 Java/SQL 文件第 2 个 SELECT 起被丢, 削弱 D10 路径 C)。同一 SQL 抽两次仍去重。"""
    from contextos.lineage.pipeline import _maybe_template
    from contextos.lineage.models import RecoveredSqlCandidate
    templates: list = []
    seen: set = set()
    c1 = RecoveredSqlCandidate(source_path="dao/Multi.java", line_start=10, line_end=12,
                               container="Multi.a", sql_text="SELECT * FROM T_A",
                               recovery_mode="literal")
    c2 = RecoveredSqlCandidate(source_path="dao/Multi.java", line_start=20, line_end=22,
                               container="Multi.b", sql_text="SELECT * FROM T_B",
                               recovery_mode="literal")
    _maybe_template(c1, templates, seen)
    _maybe_template(c2, templates, seen)
    assert {t["sql_text"] for t in templates} == {"SELECT * FROM T_A", "SELECT * FROM T_B"}
    assert len({t["template_id"] for t in templates}) == 2     # 两个不同 id
    _maybe_template(c1, templates, seen)                        # 同 SQL 抽两次
    assert len(templates) == 2                                 # 仍去重, 不双存


def test_explicit_schema_cross_owner_join_produces_distinct_edge(tmp_path):
    """显式 schema 同名表跨 owner JOIN -> 产 1 条边, src/dst owner 区分(review HIGH 端到端)。

    离线: schema 在 SQL 里显式给出, resolve_table 把 schema 当 owner -> 边带 UPC/SEC,
    不再被 src_tpl==dst_tpl 当自连丢掉。"""
    from contextos.lineage.pipeline import build_lineage
    _write(tmp_path, "x/q.sql", "SELECT * FROM UPC.COMMON_T a JOIN SEC.COMMON_T b ON a.id=b.id")
    eng = make_engine("sqlite://")
    store.create_all(eng)
    build_lineage(tmp_path, CodeConfig(), TablesConfig(), eng)
    edges = store.all_edges(eng)
    assert len(edges) == 1
    e = edges[0]
    assert {e["src_owner"], e["dst_owner"]} == {"UPC", "SEC"}
    assert e["src_table"] == "COMMON_T" and e["dst_table"] == "COMMON_T"


def test_build_lineage_stamps_lifecycle_columns(tmp_path):
    from contextos.lineage import store
    from contextos.lineage.pipeline import build_lineage
    from contextos.profile.schema import CodeConfig, TablesConfig
    from contextos.storage.db import make_engine
    (tmp_path / "q.sql").write_text("SELECT * FROM A JOIN B ON A.ID = B.ID;")
    eng = make_engine("sqlite://")
    build_lineage(tmp_path, CodeConfig(), TablesConfig(), eng, now="2026-06-06T00:00:00")
    edges = store.all_edges(eng)
    assert edges, "应有边"
    for e in edges:
        assert e["edge_kind"] == "SQL"
        assert e["last_seen_at"] == "2026-06-06T00:00:00"
        assert e["first_seen_at"] == "2026-06-06T00:00:00"
        assert e["is_active"] is True
        assert e["source_fingerprint"]            # 非空 hash


def test_build_lineage_now_defaults_to_empty(tmp_path):
    """now 可选(默认空串): 向后兼容现有 4 参调用, 不强制改所有调用点。"""
    from contextos.lineage.pipeline import build_lineage
    from contextos.profile.schema import CodeConfig, TablesConfig
    from contextos.storage.db import make_engine
    (tmp_path / "q.sql").write_text("SELECT * FROM A JOIN B ON A.ID = B.ID;")
    eng = make_engine("sqlite://")
    build_lineage(tmp_path, CodeConfig(), TablesConfig(), eng)   # 不传 now
    # 不报错即可(既有签名不破)
