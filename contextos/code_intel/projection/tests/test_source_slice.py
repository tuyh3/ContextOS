"""get_symbol_source 四护栏(spec §7): FQN 内部解析路径(class->method->field 顺序) /
resolve 后 source-root 前缀校验 / sha1 stale 标记 / 行数 cap+truncated /
redact_secrets_in_text 必过 + redacted 标记。"""
from __future__ import annotations

import hashlib

import pytest

from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection import store
from contextos.code_intel.projection.method_resolve import AmbiguousMethodFqn
from contextos.code_intel.projection.source_slice import SymbolNotFound, get_symbol_source


@pytest.fixture
def repo(engine, tmp_path):
    S.ensure_projection_schema(engine)
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    body = "\n".join([
        "package com.acme;",                       # line 0(0-based 同 LSP/投影行号)
        "public class Svc {",
        "    String url = \"jdbc:oracle:thin:user/Secret123@host:1521/db\";",
        "    public int go() {",
        "        return 1;",
        "    }",
        "}"])
    f = src / "Svc.java"
    f.write_text(body, encoding="utf-8")
    store.replace_all(engine, {
        "code_files": [{"file_path": "src/Svc.java",
                        "sha1": hashlib.sha1(f.read_bytes()).hexdigest()}],
        "code_classes": [{"class_id": "c", "class_fqn": "com.acme.Svc", "class_name": "Svc",
                          "name_lower": "svc", "source_file": "src/Svc.java",
                          "start_line": 1, "end_line": 6}],
        "code_methods": [{"method_id": "m", "class_fqn": "com.acme.Svc", "method_name": "go",
                          "name_lower": "go", "method_fqn": "com.acme.Svc.go()",
                          "source_file": "src/Svc.java", "start_line": 3, "end_line": 5}],
        "code_fields": [{"field_id": "f", "class_fqn": "com.acme.Svc", "field_name": "url",
                         "name_lower": "url", "source_file": "src/Svc.java",
                         "start_line": 2, "end_line": 2}],
    })
    return repo


def test_method_slice_by_fqn(engine, repo):
    r = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Svc.go()", max_lines=400, sensitive_patterns=[])
    assert "return 1;" in r["source"]
    assert r["file"] == "src/Svc.java"
    assert r["stale"] is False
    assert r["truncated"] is False
    assert r["line_start"] == 3 and r["line_end"] == 5


def test_field_slice_by_fqn(engine, repo):
    r = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Svc.url", max_lines=400, sensitive_patterns=[])
    assert r["line_start"] == 2


def test_unknown_fqn_raises(engine, repo):
    with pytest.raises(SymbolNotFound):
        get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Nope", max_lines=400, sensitive_patterns=[])


def test_credential_redacted(engine, repo):
    r = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Svc", max_lines=400, sensitive_patterns=[])
    assert "Secret123" not in r["source"]      # redact_secrets_in_text 必过
    assert r["redacted"] is True


def test_clean_code_not_redacted(engine, repo, tmp_path):
    """正常代码不过度打码(redacted=False, 文本原样)。"""
    f = repo / "src" / "Clean.java"
    f.write_text("package com.acme;\npublic class Clean {\n    int x = 1;\n}\n")
    with engine.begin() as conn:
        store.insert_rows_conn(conn, {
            "code_files": [{"file_path": "src/Clean.java",
                            "sha1": hashlib.sha1(f.read_bytes()).hexdigest()}],
            "code_classes": [{"class_id": "cl", "class_fqn": "com.acme.Clean",
                              "class_name": "Clean", "name_lower": "clean",
                              "source_file": "src/Clean.java",
                              "start_line": 1, "end_line": 3}]})
    r = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Clean", max_lines=400, sensitive_patterns=[])
    assert r["redacted"] is False
    assert "int x = 1;" in r["source"]


def test_stale_flag_when_file_changed(engine, repo):
    (repo / "src/Svc.java").write_text("package com.acme;\npublic class Svc {}\n")
    r = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Svc", max_lines=400, sensitive_patterns=[])
    assert r["stale"] is True


def test_truncation_cap(engine, repo):
    r = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Svc", max_lines=2, sensitive_patterns=[])
    assert r["truncated"] is True
    assert len(r["source"].splitlines()) <= 3   # 2 行 + 截断标记行


def test_path_escape_rejected(engine, repo, tmp_path):
    """投影行被污染指向 source root 外文件 -> resolve 后前缀校验拒(spec §9 上限)。"""
    (tmp_path / "outside.txt").write_text("TOP")
    from sqlalchemy import update
    with engine.begin() as conn:
        conn.execute(update(S.code_classes).values(source_file="../outside.txt"))
    with pytest.raises(SymbolNotFound, match="outside source roots"):
        get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Svc", max_lines=400, sensitive_patterns=[])


def test_custom_sensitive_patterns_applied(engine, repo):
    """护栏 4 第二层: profile 自定义敏感 key 经 sanitize_text 打码。

    fixture 形态注意: sanitize_text 的 _KV_RE 只认 key=value 形状且敏感判定看 key 段;
    `String s = "adminpin=9999"` 会先被外层 `s = "..."` 整段消费(key=s 不敏感, 引号内
    永远不被扫到, 探针实测), 所以这里把敏感词放在 key 位: `String adminpin = "9999"`。
    """
    f = repo / "src" / "Cfg.java"
    f.write_text('package com.acme;\npublic class Cfg {\n    String adminpin = "9999";\n}\n')
    with engine.begin() as conn:
        store.insert_rows_conn(conn, {
            "code_files": [{"file_path": "src/Cfg.java",
                            "sha1": hashlib.sha1(f.read_bytes()).hexdigest()}],
            "code_classes": [{"class_id": "cf", "class_fqn": "com.acme.Cfg",
                              "class_name": "Cfg", "name_lower": "cfg",
                              "source_file": "src/Cfg.java",
                              "start_line": 1, "end_line": 3}]})
    r = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Cfg", max_lines=400,
                          sensitive_patterns=["adminpin"])
    assert "9999" not in r["source"]
    assert r["redacted"] is True


# ------------------------------------------------- bare method FQN fallback


def test_bare_method_fqn_resolves(engine, repo):
    """裸方法 FQN(无签名段)经 fallback 解到唯一带签名形态, 切片与精确查相同。"""
    exact = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                              fqn="com.acme.Svc.go()", max_lines=400, sensitive_patterns=[])
    bare = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                             fqn="com.acme.Svc.go", max_lines=400, sensitive_patterns=[])
    assert bare["source"] == exact["source"]
    assert bare["line_start"] == exact["line_start"]
    assert bare["line_end"] == exact["line_end"]
    assert bare["resolved_fqn"] == "com.acme.Svc.go()"
    assert bare["fqn"] == "com.acme.Svc.go"


def test_resolved_fqn_echoes_input_on_direct_paths(engine, repo):
    """class / method-exact / field 三条既有路径: resolved_fqn == 输入 fqn。"""
    for fqn in ("com.acme.Svc", "com.acme.Svc.go()", "com.acme.Svc.url"):
        r = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                              fqn=fqn, max_lines=400, sensitive_patterns=[])
        assert r["resolved_fqn"] == fqn


def test_ambiguous_bare_method_raises_with_candidates(engine, repo):
    """裸名命中多个重载 -> AmbiguousMethodFqn, 消息列出全部签名 + 指引。"""
    with engine.begin() as conn:
        store.insert_rows_conn(conn, {"code_methods": [
            {"method_id": "m2", "class_fqn": "com.acme.Svc", "method_name": "go",
             "name_lower": "go", "method_fqn": "com.acme.Svc.go(int)",
             "source_file": "src/Svc.java", "start_line": 3, "end_line": 5}]})
    with pytest.raises(AmbiguousMethodFqn) as ei:
        get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Svc.go", max_lines=400, sensitive_patterns=[])
    msg = str(ei.value)
    assert "com.acme.Svc.go()" in msg
    assert "com.acme.Svc.go(int)" in msg
    assert "pass a signature-qualified FQN" in msg
    assert ei.value.fqn == "com.acme.Svc.go"
    assert ei.value.candidates == ["com.acme.Svc.go()", "com.acme.Svc.go(int)"]


def test_duplicate_rows_same_signature_not_ambiguous(engine, repo):
    """同一 method_fqn 多行(vendored 类多模块重复索引)-> DISTINCT 收敛为一个候选,
    裸查不比精确查更严(精确查同样 .first() 任取一行)。"""
    dup = repo / "src" / "vendorcopy" / "Svc.java"
    dup.parent.mkdir(parents=True)
    dup.write_text((repo / "src/Svc.java").read_text(encoding="utf-8"), encoding="utf-8")
    with engine.begin() as conn:
        store.insert_rows_conn(conn, {
            "code_files": [{"file_path": "src/vendorcopy/Svc.java",
                            "sha1": hashlib.sha1(dup.read_bytes()).hexdigest()}],
            "code_methods": [
                {"method_id": "mdup", "class_fqn": "com.acme.Svc", "method_name": "go",
                 "name_lower": "go", "method_fqn": "com.acme.Svc.go()",
                 "source_file": "src/vendorcopy/Svc.java", "start_line": 3, "end_line": 5}]})
    r = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Svc.go", max_lines=400, sensitive_patterns=[])
    assert r["resolved_fqn"] == "com.acme.Svc.go()"
    assert "return 1;" in r["source"]


def test_bare_resolution_escapes_like_metachars(engine, repo):
    """方法名含 '_'(SQL LIKE 单字符通配)-> 必须 autoescape, 否则 do_run 会
    误配 doXrun 报歧义。"""
    body = "\n".join(["package com.acme;", "public class Job {",
                      "    void do_run() {", "    }",
                      "    void doXrun() {", "    }", "}"])
    f = repo / "src" / "Job.java"
    f.write_text(body, encoding="utf-8")
    with engine.begin() as conn:
        store.insert_rows_conn(conn, {
            "code_files": [{"file_path": "src/Job.java",
                            "sha1": hashlib.sha1(f.read_bytes()).hexdigest()}],
            "code_methods": [
                {"method_id": "j1", "class_fqn": "com.acme.Job", "method_name": "do_run",
                 "name_lower": "do_run", "method_fqn": "com.acme.Job.do_run()",
                 "source_file": "src/Job.java", "start_line": 2, "end_line": 3},
                {"method_id": "j2", "class_fqn": "com.acme.Job", "method_name": "doXrun",
                 "name_lower": "doxrun", "method_fqn": "com.acme.Job.doXrun()",
                 "source_file": "src/Job.java", "start_line": 4, "end_line": 5}]})
    r = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Job.do_run", max_lines=400, sensitive_patterns=[])
    assert r["resolved_fqn"] == "com.acme.Job.do_run()"
    assert r["line_start"] == 2 and r["line_end"] == 3


def test_field_beats_bare_method_fallback(engine, repo):
    """同名 field 'count' 与方法 'count()': 裸 '...count' 走既有 field 路径
    (fallback 是最后一站, 不抢既有优先级)。"""
    body = "\n".join(["package com.acme;", "public class Ctr {",
                      "    int count = 0;",
                      "    int count() {", "        return count;", "    }", "}"])
    f = repo / "src" / "Ctr.java"
    f.write_text(body, encoding="utf-8")
    with engine.begin() as conn:
        store.insert_rows_conn(conn, {
            "code_files": [{"file_path": "src/Ctr.java",
                            "sha1": hashlib.sha1(f.read_bytes()).hexdigest()}],
            "code_fields": [
                {"field_id": "fc", "class_fqn": "com.acme.Ctr", "field_name": "count",
                 "name_lower": "count", "source_file": "src/Ctr.java",
                 "start_line": 2, "end_line": 2}],
            "code_methods": [
                {"method_id": "mc", "class_fqn": "com.acme.Ctr", "method_name": "count",
                 "name_lower": "count", "method_fqn": "com.acme.Ctr.count()",
                 "source_file": "src/Ctr.java", "start_line": 3, "end_line": 5}]})
    r = get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Ctr.count", max_lines=400, sensitive_patterns=[])
    assert r["line_start"] == 2 and r["line_end"] == 2     # field slice, not method
    assert r["resolved_fqn"] == "com.acme.Ctr.count"


def test_bare_unknown_method_still_symbol_not_found(engine, repo):
    with pytest.raises(SymbolNotFound):
        get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Svc.nope", max_lines=400, sensitive_patterns=[])


def test_bare_resolved_missing_anchor_reports_resolved_fqn(engine, repo):
    """裸名 fallback 命中但该行 source_file 为空 -> 错误信息报 resolved(带签名)形态,
    不报裸输入(报实际命中的行身份才好排查)。"""
    with engine.begin() as conn:
        store.insert_rows_conn(conn, {
            "code_methods": [{"method_id": "mg", "class_fqn": "com.acme.Ghost",
                              "method_name": "run", "name_lower": "run",
                              "method_fqn": "com.acme.Ghost.run()",
                              "source_file": "", "start_line": 1, "end_line": 2}]})
    with pytest.raises(SymbolNotFound, match=r"com\.acme\.Ghost\.run\(\)"):
        get_symbol_source(engine, repo_root=repo, source_roots=[repo / "src"],
                          fqn="com.acme.Ghost.run", max_lines=400, sensitive_patterns=[])
