"""jsonl_load: camel/snake 双命名容错 / source_file 派生链(classes -> methods -> calls,
inheritance 经 sub) / lang 全标 java / 列表字段转 JSON 文本 / 路径相对化 / FQN # 归一点分。"""
from __future__ import annotations

import json
from pathlib import Path

from contextos.code_intel.projection.jsonl_load import load_all_rows


def _write_jsonl(d: Path, name: str, recs: list[dict]) -> None:
    (d / name).write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")


def _fixture_dir(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    _write_jsonl(out, "files.jsonl", [
        {"path": "/repo/src/com/acme/A.java", "module": "m1",
         "packageName": "com.acme", "sha1": "aa" * 20}])
    _write_jsonl(out, "classes.jsonl", [
        {"classId": "c1", "classFqn": "com.acme.A", "className": "A",
         "packageName": "com.acme", "filePath": "/repo/src/com/acme/A.java",
         "kind": "class", "superclass": "", "interfaces": ["com.acme.I"],
         "modifiers": ["public"], "annotations": [], "startLine": 2, "endLine": 9}])
    _write_jsonl(out, "methods.jsonl", [
        {"methodId": "m1", "classFqn": "com.acme.A", "methodName": "twice",
         "signature": "twice(int)", "methodFqn": "com.acme.A#twice(int)",
         "returnType": "int", "paramTypes": ["int"], "paramNames": ["x"],
         "modifiers": ["public"], "annotations": [], "isConstructor": False,
         "startLine": 3, "endLine": 5}])
    _write_jsonl(out, "fields.jsonl", [
        {"fieldId": "f1", "class_fqn": "com.acme.A", "field_name": "LIMIT",
         "field_type": "int", "modifiers": ["static", "final"], "annotations": [],
         "start_line": 2, "end_line": 2}])
    _write_jsonl(out, "calls.jsonl", [
        {"callId": "k1", "callerMethodFqn": "com.acme.A#twice(int)",
         "calleeClassFqn": "com.acme.B", "calleeMethodName": "run",
         "calleeMethodFqn": "com.acme.B#run()", "dispatchKind": "virtual",
         "lineNo": 4, "resolved": True}])
    _write_jsonl(out, "inheritance.jsonl", [
        {"subClassFqn": "com.acme.A", "superClassFqn": "com.acme.I",
         "relationType": "implements"}])
    _write_jsonl(out, "references.jsonl", [
        {"sourceFqn": "com.acme.A", "sourceFile": "/repo/src/com/acme/A.java",
         "targetFqn": "com.acme.I", "targetKind": "type", "refKind": "implements",
         "lineNo": 2, "columnNo": 10}])
    return out


def test_load_all_rows_basics(tmp_path):
    rows = load_all_rows(_fixture_dir(tmp_path), repo_root=Path("/repo"))
    cls = rows["code_classes"][0]
    assert cls["class_fqn"] == "com.acme.A"
    assert cls["name_lower"] == "a"
    assert cls["source_file"] == "src/com/acme/A.java"      # 相对仓根
    assert cls["interfaces_json"] == json.dumps(["com.acme.I"], ensure_ascii=False)
    assert cls["lang"] == "java"


def test_fqn_hash_normalized_to_dot(tmp_path):
    """jar 方法 FQN 用 #(com.acme.A#twice(int)); loader 归一为点分(全链身份格式)。"""
    rows = load_all_rows(_fixture_dir(tmp_path), repo_root=Path("/repo"))
    assert rows["code_methods"][0]["method_fqn"] == "com.acme.A.twice(int)"
    assert rows["code_calls"][0]["caller_method_fqn"] == "com.acme.A.twice(int)"
    assert rows["code_calls"][0]["callee_method_fqn"] == "com.acme.B.run()"


def test_source_file_derived_for_methods_calls_inheritance(tmp_path):
    rows = load_all_rows(_fixture_dir(tmp_path), repo_root=Path("/repo"))
    assert rows["code_methods"][0]["source_file"] == "src/com/acme/A.java"   # 经 class_fqn
    assert rows["code_calls"][0]["source_file"] == "src/com/acme/A.java"     # 经 caller method_fqn(归一后)
    assert rows["code_inheritance"][0]["source_file"] == "src/com/acme/A.java"  # 经 sub_class_fqn
    assert rows["code_files"][0]["file_path"] == "src/com/acme/A.java"
    assert rows["code_references"][0]["source_file"] == "src/com/acme/A.java"


def test_files_sha1_computed_from_disk_when_missing(tmp_path):
    """F2 回归: 真 jar files.jsonl 不带 sha1 字段 -> loader 对磁盘上存在的文件补算
    sha1(否则 code_files.sha1 恒空, 增量基准必失配, 永久 full_rebuild 循环)。
    JSONL 自带 sha1 则信 JSONL 不重复算; 磁盘不存在保持 ""。"""
    import hashlib

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    java = repo / "src" / "A.java"
    java.write_text("class A {}\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    _write_jsonl(out, "files.jsonl", [
        {"path": str(java), "module": "m1", "packageName": "com.acme"},  # 无 sha1 字段
        {"path": str(repo / "src" / "Gone.java"), "module": "m1"},       # 磁盘不存在
        {"path": str(java), "module": "m1", "sha1": "ff" * 20},          # JSONL 自带
    ])
    rows = load_all_rows(out, repo_root=repo)
    by_idx = rows["code_files"]
    assert by_idx[0]["sha1"] == hashlib.sha1(java.read_bytes()).hexdigest()
    assert by_idx[1]["sha1"] == ""
    assert by_idx[2]["sha1"] == "ff" * 20  # 信 JSONL, 不重复算


def test_local_class_empty_fqn_rows_dropped(tmp_path):
    """F3 回归: JDT getQualifiedName 对局部类返回 "" -> classes/methods/fields 的
    空 class_fqn 记录跳过不进投影(空 FQN 行会撞唯一约束/污染 cls_file 映射)。"""
    out = tmp_path / "out"
    out.mkdir()
    _write_jsonl(out, "classes.jsonl", [
        {"classId": "C1", "classFqn": "com.acme.A", "className": "A",
         "filePath": "/repo/src/A.java"},
        {"classId": "C2", "classFqn": "", "className": "LocalHandler",
         "filePath": "/repo/src/A.java"},
    ])
    _write_jsonl(out, "methods.jsonl", [
        {"methodId": "M1", "classFqn": "com.acme.A", "methodName": "run",
         "methodFqn": "com.acme.A#run()"},
        {"methodId": "M2", "classFqn": "", "methodName": "handle",
         "methodFqn": "#handle()"},
    ])
    _write_jsonl(out, "fields.jsonl", [
        {"fieldId": "F1", "classFqn": "", "fieldName": "tmp"},
    ])
    rows = load_all_rows(out, repo_root=Path("/repo"))
    assert [c["class_fqn"] for c in rows["code_classes"]] == ["com.acme.A"]
    assert [m["method_fqn"] for m in rows["code_methods"]] == ["com.acme.A.run()"]
    assert rows["code_fields"] == []


def test_line_numbers_normalized_to_0_based(tmp_path):
    """HIGH-1 回归: jar 行号 1-based(JDT getLineNumber), loader 单点归一 0-based
    (LSP/投影契约, source_slice 0-based 切片 / searcher LSP range 直接消费)。
    column 来自 JDT getColumnNumber 本就 0-based(SymbolEmitter 核实), 原样保留。"""
    rows = load_all_rows(_fixture_dir(tmp_path), repo_root=Path("/repo"))
    cls = rows["code_classes"][0]
    assert (cls["start_line"], cls["end_line"]) == (1, 8)        # jar 2/9 -> 1/8
    m = rows["code_methods"][0]
    assert (m["start_line"], m["end_line"]) == (2, 4)            # jar 3/5 -> 2/4
    f = rows["code_fields"][0]
    assert (f["start_line"], f["end_line"]) == (1, 1)            # jar 2/2 -> 1/1
    assert rows["code_calls"][0]["line_no"] == 3                 # jar 4 -> 3
    ref = rows["code_references"][0]
    assert ref["line_no"] == 1                                   # jar 2 -> 1
    assert ref["column_no"] == 10                                # 0-based 原样不动


def test_line_number_sentinels_floor_at_zero(tmp_path):
    """jar 对未知行给 0 / 缺省 / -1 哨兵(JDT getLineNumber 失败档)-> 归一后
    floor 0, 不出负数。普通行(startLine 7)归一 6。"""
    out = tmp_path / "out"
    out.mkdir()
    _write_jsonl(out, "classes.jsonl", [
        {"classId": "c1", "classFqn": "com.acme.A", "className": "A",
         "filePath": "/repo/src/A.java"},                              # 行号缺省
        {"classId": "c2", "classFqn": "com.acme.B", "className": "B",
         "filePath": "/repo/src/B.java", "startLine": -1, "endLine": 0},  # 哨兵
        {"classId": "c3", "classFqn": "com.acme.C", "className": "C",
         "filePath": "/repo/src/C.java", "startLine": 7, "endLine": 7}])
    rows = load_all_rows(out, repo_root=Path("/repo"))
    a, b, c = rows["code_classes"]
    assert (a["start_line"], a["end_line"]) == (0, 0)
    assert (b["start_line"], b["end_line"]) == (0, 0)
    assert (c["start_line"], c["end_line"]) == (6, 6)


def test_bool_and_missing_tolerance(tmp_path):
    rows = load_all_rows(_fixture_dir(tmp_path), repo_root=Path("/repo"))
    assert rows["code_methods"][0]["is_constructor"] == 0
    assert rows["code_calls"][0]["resolved"] == 1
    # table_refs.jsonl 不存在(v1 预期空) -> 空列表而非崩
    assert rows["code_table_refs"] == []


# --- HIGH-2(最终 review): 复合 PK 逃逸 -> loader 层修类(空 sub 丢 + 复合键去重)---
# 设计思路(merge-review v3 修订): inheritance/table_refs 已是 row_id 代理 PK,
# 重复行不再撞约束; loader 去重的语义变为"同文件去噪"——key 含 source_file,
# 同文件同 (sub,super)/(method,table) 重复保首行, **跨文件**重复合法共存
# (重复 FQN 世界, 增量按文件删插)。空 sub 行仍跳过(局部类)。
# 评分: 同文件重复收敛单行 / 空 sub 不出现 / 跨文件共存归 incremental 回归测试。


def test_inheritance_empty_sub_dropped_and_dedup(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _write_jsonl(out, "inheritance.jsonl", [
        {"subClassFqn": "com.acme.A", "superClassFqn": "com.acme.I",
         "relationType": "implements"},
        {"subClassFqn": "com.acme.A", "superClassFqn": "com.acme.I",
         "relationType": "implements"},                       # jar 重复 -> 去重保首行
        {"subClassFqn": "", "superClassFqn": "com.acme.I",
         "relationType": "implements"},                       # 局部类空 sub -> 丢
        {"subClassFqn": "com.acme.B", "superClassFqn": "com.acme.I",
         "relationType": "implements"}])
    rows = load_all_rows(out, repo_root=Path("/repo"))
    inh = rows["code_inheritance"]
    assert len(inh) == 2
    assert [r["sub_class_fqn"] for r in inh] == ["com.acme.A", "com.acme.B"]


def test_table_refs_composite_pk_dedup(tmp_path):
    """code_table_refs 同文件重复 (method_fqn, table_name) 去重保首行(v3 代理 PK 后为去噪语义)。"""
    out = tmp_path / "out"
    out.mkdir()
    _write_jsonl(out, "table_refs.jsonl", [
        {"methodFqn": "com.acme.A#run()", "tableName": "T_X", "refKind": "select"},
        {"methodFqn": "com.acme.A#run()", "tableName": "T_X", "refKind": "update"},  # PK 撞 -> 丢
        {"methodFqn": "com.acme.A#run()", "tableName": "T_Y", "refKind": "select"}])
    rows = load_all_rows(out, repo_root=Path("/repo"))
    trs = rows["code_table_refs"]
    assert len(trs) == 2
    assert {t["table_name"] for t in trs} == {"T_X", "T_Y"}
    assert trs[0]["ref_kind"] == "select"                     # 保首行


def test_out_of_repo_path_kept_as_posix_absolute(tmp_path):
    """Windows 阶段2 附录B: _rel 对仓外绝对路径也走 as_posix()(与 incremental
    ._scan_source_roots 锚口径一致), 不再是裸 str()。POSIX 上两者恒等, 但这
    锁定实现走 as_posix 分支(而非侥幸只测到 in-repo 分支)。"""
    out = tmp_path / "out"
    out.mkdir()
    ext = tmp_path / "external"
    ext.mkdir()
    java = ext / "X.java"
    java.write_text("class X {}", encoding="utf-8")
    _write_jsonl(out, "classes.jsonl", [
        {"classId": "c1", "classFqn": "com.acme.X", "className": "X",
         "filePath": str(java), "kind": "class"}])
    rows = load_all_rows(out, repo_root=tmp_path / "repo")   # repo_root 与 java 无关系(仓外)
    assert rows["code_classes"][0]["source_file"] == java.as_posix()
