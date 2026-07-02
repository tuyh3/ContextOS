import shutil

import pytest

_HAS_RG = shutil.which("rg") is not None


def _make_corpus(tmp_path):
    (tmp_path / "order").mkdir()
    (tmp_path / "order" / "a.md").write_text(
        "line1\nDynamicChargingSVImpl 动态计费\nCONF_PROVINCE_TAX\nline4\n", encoding="utf-8"
    )
    (tmp_path / "order" / "b.md").write_text("unrelated\ncontent\n", encoding="utf-8")
    return tmp_path


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_ripgrep_hits_finds_terms(tmp_path):
    from contextos.recall.sparse import ripgrep_hits
    root = _make_corpus(tmp_path)
    hits = ripgrep_hits(["CONF_PROVINCE_TAX", "DynamicChargingSVImpl"], root)
    paths = {h.rel_path for h in hits}
    assert "order/a.md" in paths
    assert "order/b.md" not in paths
    # 命中行号正确(1-based)
    linenos = sorted(h.lineno for h in hits if h.rel_path == "order/a.md")
    assert 2 in linenos and 3 in linenos


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_ripgrep_hits_empty_patterns(tmp_path):
    from contextos.recall.sparse import ripgrep_hits
    assert ripgrep_hits([], _make_corpus(tmp_path)) == []


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_ripgrep_hits_path_prefix_filter(tmp_path):
    from contextos.recall.sparse import ripgrep_hits
    root = tmp_path
    (root / "order").mkdir()
    (root / "billing").mkdir()
    (root / "order" / "x.md").write_text("TERM here\n", encoding="utf-8")
    (root / "billing" / "y.md").write_text("TERM here\n", encoding="utf-8")
    hits = ripgrep_hits(["TERM"], root, path_prefixes=["order"])
    assert {h.rel_path for h in hits} == {"order/x.md"}


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_ripgrep_hits_literal_not_regex(tmp_path):
    """key_entities 是字面标识符: metachar(. ( ) [)按字面匹配, 不当正则(review M1)。"""
    from contextos.recall.sparse import ripgrep_hits
    (tmp_path / "c.md").write_text(
        "aXc should-not-match-dot-as-wildcard\n"      # line1: regex 'a.c' 会误中, 字面不该中
        "a.c literal-dot\n"                            # line2: 字面 'a.c' 该中
        "getUserById(id) method-signature\n"          # line3: 含 () 的方法签名该字面命中
        "arr[0] bracket-ref\n",                        # line4: 含 [ 的数组引用该字面命中
        encoding="utf-8",
    )
    hits = ripgrep_hits(["a.c", "getUserById(id)", "arr[0"], tmp_path)
    matched = {h.lineno for h in hits}
    assert 1 not in matched          # 'a.c' 字面不误中 'aXc'
    assert 2 in matched              # 'a.c' 命中字面 a.c
    assert 3 in matched              # 'getUserById(id)' 字面命中(正则会因 (id) 分组漏掉)
    assert 4 in matched              # 'arr[' 字面命中(正则未闭合字符类会 exit 2 全盘 miss)


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_ripgrep_hits_bad_metachar_does_not_silently_drop_others(tmp_path):
    """单个含非法正则 metachar 的词不能拖垮其它有效词(review M1 最严重情形)。"""
    from contextos.recall.sparse import ripgrep_hits
    (tmp_path / "d.md").write_text("CONF_PROVINCE_TAX present\narr[0 here\n", encoding="utf-8")
    # 'arr[0' 作正则是未闭合字符类(旧实现 -> exit 2 + 空输出 -> 静默丢掉 CONF_*)
    hits = ripgrep_hits(["arr[0", "CONF_PROVINCE_TAX"], tmp_path)
    lines = {h.line for h in hits}
    assert any("CONF_PROVINCE_TAX" in ln for ln in lines)   # 有效词仍被找到
    assert any("arr[0" in ln for ln in lines)               # 非法 metachar 词也字面命中


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_ripgrep_hits_relative_root_with_prefix(tmp_path, monkeypatch):
    """relative root + path_prefixes 不能把 root 叠加两次。

    回归(live 实测 2026-06-30): subprocess 用 cwd=root, 故 search_paths 必须相对 root(就是
    path_prefixes 本身), 不能再拼 root/prefix —— 否则 cwd=root 下又找 root/prefix = root 被算
    两次 -> rg ENOENT(exit 2) -> RuntimeError。线上 data_dir='database/materialized' 是相对路径,
    confirmed-cases corpus(首个用 prefix scope 的子集)因此静默召回为空。绝对 root 旧实现侥幸
    可用(绝对 search path 忽略 cwd), 故旧 absolute-tmp_path 单测漏掉本 bug。"""
    from contextos.recall.sparse import ripgrep_hits
    (tmp_path / "mat" / "confirmed-cases").mkdir(parents=True)
    (tmp_path / "mat" / "confirmed-cases" / "case.md").write_text(
        "deferred_charge NEEDLE here\n", encoding="utf-8")
    (tmp_path / "mat" / "other").mkdir()
    (tmp_path / "mat" / "other" / "z.md").write_text("NEEDLE elsewhere\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    hits = ripgrep_hits(["NEEDLE"], "mat", path_prefixes=["confirmed-cases"])  # relative root
    assert {h.rel_path for h in hits} == {"confirmed-cases/case.md"}  # 限定到子集 + 找到


@pytest.mark.cmd_boundary
@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_ripgrep_hits_space_in_path(tmp_path):
    """NUL 切分: 空格文件名不碎(text-mode 旧切法对空格也行, 但 bytes 切是协议保证)。"""
    from contextos.recall.sparse import ripgrep_hits
    (tmp_path / "a b.md").write_text("NEEDLE here\n", encoding="utf-8")
    hits = ripgrep_hits(["NEEDLE"], tmp_path)
    assert any(h.rel_path == "a b.md" for h in hits)


@pytest.mark.cmd_boundary
@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_ripgrep_hits_non_ascii_path(tmp_path):
    """中文路径(UTF-8 bytes)经 os.fsdecode 还原。"""
    from contextos.recall.sparse import ripgrep_hits
    (tmp_path / "中文.md").write_text("NEEDLE here\n", encoding="utf-8")
    hits = ripgrep_hits(["NEEDLE"], tmp_path)
    assert any("中文.md" in h.rel_path for h in hits)


@pytest.mark.cmd_boundary
@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_ripgrep_hits_content_colon_and_crlf(tmp_path):
    """内容含冒号保留(首冒号只切 lineno)+ CRLF 文件 content 尾 \\r 被 rstrip。"""
    from contextos.recall.sparse import ripgrep_hits
    (tmp_path / "x.md").write_bytes(b"key: NEEDLE: value\r\n")   # 内容含冒号 + CRLF
    hits = [h for h in ripgrep_hits(["NEEDLE"], tmp_path) if h.rel_path == "x.md"]
    assert len(hits) == 1
    assert hits[0].lineno == 1
    assert hits[0].line == "key: NEEDLE: value"   # 冒号保留, \r 去掉


@pytest.mark.cmd_boundary
@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_ripgrep_hits_binary_file_no_crash_no_false_hit(tmp_path):
    """含 NUL 文件不致崩 / 不产假 hit。注意: sparse 走**目录递归扫描**(search_paths=['.']),
    rg 在此模式对含真 NUL 文件**静默跳过**(stdout 不出 'binary file matches' 行)—— 故本测试
    验证的是"含 NUL 文件不污染结果",而非守卫被触发。parser 守卫(if b'\\0' not in record)真正
    被触发处是 source_search(显式文件列表),见 test_source_search.py 同名验证(review 第七轮 P2)。"""
    from contextos.recall.sparse import ripgrep_hits
    (tmp_path / "bin.java").write_bytes(b"alpha NEEDLE\x00beta NEEDLE\n")   # 含真 NUL -> rg 判 binary
    (tmp_path / "ok.md").write_text("plain NEEDLE here\n", encoding="utf-8")
    hits = ripgrep_hits(["NEEDLE"], tmp_path)        # 不得抛
    paths = {h.rel_path for h in hits}
    assert "ok.md" in paths                          # 正常文本命中不受影响
    assert all("binary file matches" not in h.line for h in hits)   # 无假 summary hit
