"""Layer 2 源码扫描测试。用 tmp_path 造小 fixture repo。"""
from contextos.profile.schema import CodeConfig, DaoSqlPattern


def _write(root, rel, content):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_scan_classifies_java_and_sql(tmp_path):
    from contextos.lineage.source_scan import scan_sources
    _write(tmp_path, "order/impl/src/main/SELECT_BY_ID.sql", "SELECT * FROM T")
    _write(tmp_path, "order/Foo.java", "class Foo {}")
    _write(tmp_path, "order/other/q.sql", "SELECT 1 FROM DUAL")
    code = CodeConfig(dao_sql_patterns=[
        DaoSqlPattern(path_contains=["/impl/", "/src/main/"], conjunction="all")])
    sources = scan_sources(tmp_path, code)
    by_path = {s.path: s for s in sources}
    assert by_path["order/impl/src/main/SELECT_BY_ID.sql"].category == "dao_sql"
    assert by_path["order/other/q.sql"].category == "other_sql"
    assert by_path["order/Foo.java"].language == "java"
    assert by_path["order/Foo.java"].module == "order"


def test_empty_patterns_all_other_sql(tmp_path):
    """空 dao_sql_patterns -> 全 .sql 当 other_sql(退化默认)。"""
    from contextos.lineage.source_scan import scan_sources
    _write(tmp_path, "x/impl/src/main/Q.sql", "SELECT 1 FROM DUAL")
    sources = scan_sources(tmp_path, CodeConfig())
    assert sources[0].category == "other_sql"


def test_conjunction_any(tmp_path):
    from contextos.lineage.source_scan import scan_sources
    _write(tmp_path, "x/dao/Q.sql", "SELECT 1 FROM DUAL")
    code = CodeConfig(dao_sql_patterns=[
        DaoSqlPattern(path_contains=["/dao/", "/never/"], conjunction="any")])
    sources = scan_sources(tmp_path, code)
    assert sources[0].category == "dao_sql"


def test_exclude_dirs(tmp_path):
    from contextos.lineage.source_scan import scan_sources
    _write(tmp_path, "build/Gen.java", "class Gen {}")
    _write(tmp_path, "src/Real.java", "class Real {}")
    sources = scan_sources(tmp_path, CodeConfig(exclude_dirs=["build"]))
    paths = [s.path for s in sources]
    assert "src/Real.java" in paths
    assert "build/Gen.java" not in paths


def test_source_roots_scopes_scan(tmp_path):
    """source_roots 非空 -> 只扫这些子目录。"""
    from contextos.lineage.source_scan import scan_sources
    _write(tmp_path, "keep/A.java", "class A {}")
    _write(tmp_path, "skip/B.java", "class B {}")
    sources = scan_sources(tmp_path, CodeConfig(source_roots=["keep"]))
    paths = [s.path for s in sources]
    assert paths == ["keep/A.java"]


def test_out_of_repo_root_anchor_is_posix(tmp_path):
    """Windows 阶段2 附录B: 仓外 source root(profile 允许绝对路径指仓外)扫描时,
    relative_to(repo_root) 抛 ValueError 的仓外分支锚点走 as_posix()(与
    jsonl_load._rel / incremental._scan_source_roots 同口径), 不裸 str()。"""
    from contextos.lineage.source_scan import scan_sources
    repo = tmp_path / "repo"
    repo.mkdir()
    ext = tmp_path / "external"
    _write(ext, "X.java", "class X {}")
    sources = scan_sources(repo, CodeConfig(source_roots=[str(ext)]))
    assert sources[0].path == (ext / "X.java").as_posix()
