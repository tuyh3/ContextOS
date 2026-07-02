def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")


def test_iter_source_globs_and_relpaths(tmp_path):
    from contextos.profile.schema import SourceConfig
    from contextos.corpus.connectors import iter_source
    _touch(tmp_path / "doc" / "a.md")
    _touch(tmp_path / "doc" / "b.docx")
    _touch(tmp_path / "doc" / "skip.txt")
    src = SourceConfig(type="dir", location=str(tmp_path), glob=["**/*.md", "**/*.docx"])
    items = sorted(iter_source(src), key=lambda it: it.rel_path)
    rels = [it.rel_path for it in items]
    assert "doc/a.md" in rels
    assert "doc/b.docx" in rels
    assert "doc/skip.txt" not in rels
    fmts = {it.rel_path: it.fmt for it in items}
    assert fmts["doc/a.md"] == "md"
    assert fmts["doc/b.docx"] == "docx"


def test_iter_source_skips_ms_office_lock_files(tmp_path):
    """跳过 MS Office 的 ~$ 锁定/属主临时文件(开着 Word 时生成的 ~$xxx.docx,
    不是真文档, 物化会 'File is not a zip file' 失败 -> 让 corpus 维一直 degraded)。"""
    from contextos.profile.schema import SourceConfig
    from contextos.corpus.connectors import iter_source
    _touch(tmp_path / "doc" / "real.docx")
    _touch(tmp_path / "doc" / "~$real.docx")        # Word 锁定临时文件
    _touch(tmp_path / "doc" / "~$lock.md")          # 其它格式的锁定文件也跳
    src = SourceConfig(type="dir", location=str(tmp_path), glob=["**/*.md", "**/*.docx"])
    rels = [it.rel_path for it in iter_source(src)]
    assert "doc/real.docx" in rels
    assert "doc/~$real.docx" not in rels
    assert "doc/~$lock.md" not in rels


def test_iter_source_dedups_overlapping_globs(tmp_path):
    from contextos.profile.schema import SourceConfig
    from contextos.corpus.connectors import iter_source
    _touch(tmp_path / "a.md")
    src = SourceConfig(type="git", location=str(tmp_path), glob=["**/*.md", "*.md"])
    rels = [it.rel_path for it in iter_source(src)]
    assert rels.count("a.md") == 1
