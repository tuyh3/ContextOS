"""jar JSONL(8 个文件, 见 JSONL_NAMES)-> code_* 行 dict。

- 键名 camelCase/snake_case 双容错(LP emit_sqlite 同款 rec.get 链)
- source_file 派生链: classes(自带 filePath) -> methods(经 class_fqn) ->
  calls(经 caller method_fqn -> methods 映射) ; inheritance 经 sub_class_fqn
- FQN 归一 chokepoint: jar 方法 FQN 用 #(com.acme.A#twice(int)), 统一替换为
  点分(全链身份格式: ProviderCandidate.target / read_symbol / lookup_calls)
- 行号归一 chokepoint: jar 行号 1-based(JDT cu.getLineNumber), 本层统一 -1 归一为
  0-based(LSP/投影契约: source_slice 0-based 切片 / searcher LSP range 直接消费);
  column 来自 JDT cu.getColumnNumber 本就 0-based(SymbolEmitter 核实), 不动
- 路径一律相对 repo_root(投影内不存绝对路径, 仓库挪位置可重建)
- lang 全标 'java'(D5: 扩展点在列)
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# jar 全部已知输出文件清单(SSOT): loader 按此读, indexer_runner 按此清残留。
# 新增 jar 输出文件时只改这里(及对应 import_ 段), 清残留自动跟上。
JSONL_NAMES = ("files.jsonl", "classes.jsonl", "methods.jsonl", "fields.jsonl",
               "calls.jsonl", "inheritance.jsonl", "references.jsonl", "table_refs.jsonl")


def _load_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _g(rec: dict[str, Any], camel: str, snake: str, default: Any = "") -> Any:
    v = rec.get(camel, rec.get(snake, default))
    return default if v is None else v


def _j(val: Any) -> str:
    if isinstance(val, (list, dict)):
        return json.dumps(val, ensure_ascii=False)
    return val or "[]"


def _line0(v: Any) -> int:
    """jar 行号 1-based(JDT getLineNumber)-> 0-based(LSP/投影契约)。
    jar 对未知行给 0 / 缺省 / -1 哨兵(getLineNumber 失败档): -1 后 floor 0 不出负数。"""
    return max(int(v or 0) - 1, 0)


def _norm_fqn(fqn: str) -> str:
    """jar 方法 FQN 用 # 分隔类与方法; 全链身份格式是点分 -> loader 统一归一。
    类 FQN 不含 # 时恒等(统一过一遍, 防 jar 未来在内部类上用 # 的意外)。"""
    return fqn.replace("#", ".") if fqn else fqn


def _rel(p: str, repo_root: Path, stats: dict[str, Any] | None = None) -> str:
    if not p:
        return ""
    try:
        return Path(p).relative_to(repo_root).as_posix()
    except ValueError:
        # repo 外路径: 仓外 source root(profile 允许)的文件属合法来源, 保留绝对路径
        # (与 incremental._scan_source_roots 锚口径一致); 计数 + 末尾汇总告警(F4 不静默)
        if stats is not None:
            stats["count"] += 1
            if not stats["first"]:
                stats["first"] = p
        return Path(p).as_posix()


def load_all_rows(out_dir: Path, *, repo_root: Path) -> dict[str, list[dict[str, Any]]]:
    """读 7 个 JSONL, 返回 {表名: rows}。一次性内存装载(大仓百万行级 references 的
    dict 占内存 ~GB 级; 若实测吃紧, 后续把 references 改流式分批 —— 接口不变)。"""
    rel_stats: dict[str, Any] = {"count": 0, "first": ""}
    # F3: JDT getQualifiedName 对局部类返回 "" -> 空 class_fqn 记录跳过(不进投影,
    # 不污染 cls_file 映射), 末尾汇总告警一条, 不逐行刷屏。
    dropped: dict[str, int] = {"classes": 0, "methods": 0, "fields": 0, "inheritance": 0}

    classes: list[dict[str, Any]] = []
    cls_file: dict[str, str] = {}     # class_fqn -> source_file(派生链第 1 级)
    for r in _load_jsonl(out_dir / "classes.jsonl"):
        fqn = _norm_fqn(_g(r, "classFqn", "class_fqn"))
        if not fqn:
            dropped["classes"] += 1
            continue
        name = _g(r, "className", "class_name")
        sf = _rel(_g(r, "filePath", "file_path"), repo_root, rel_stats)
        cls_file[fqn] = sf
        classes.append({
            "class_id": _g(r, "classId", "class_id"), "lang": "java",
            "class_fqn": fqn, "class_name": name, "name_lower": name.lower(),
            "package_name": _g(r, "packageName", "package_name"),
            "source_file": sf, "kind": _g(r, "kind", "kind"),
            "superclass": _norm_fqn(_g(r, "superclass", "superclass")),
            "interfaces_json": _j(_g(r, "interfaces", "interfaces", [])),
            "modifiers_json": _j(_g(r, "modifiers", "modifiers", [])),
            "annotations_json": _j(_g(r, "annotations", "annotations", [])),
            "start_line": _line0(_g(r, "startLine", "start_line", 0)),
            "end_line": _line0(_g(r, "endLine", "end_line", 0)),
        })

    methods: list[dict[str, Any]] = []
    meth_file: dict[str, str] = {}    # method_fqn(归一后) -> source_file(派生链第 2 级)
    for r in _load_jsonl(out_dir / "methods.jsonl"):
        cfqn = _norm_fqn(_g(r, "classFqn", "class_fqn"))
        if not cfqn:
            dropped["methods"] += 1
            continue
        mfqn = _norm_fqn(_g(r, "methodFqn", "method_fqn"))
        name = _g(r, "methodName", "method_name")
        sf = cls_file.get(cfqn, "")
        if mfqn:
            meth_file[mfqn] = sf
        methods.append({
            "method_id": _g(r, "methodId", "method_id"), "lang": "java",
            "class_fqn": cfqn, "method_name": name, "name_lower": name.lower(),
            "signature": _g(r, "signature", "signature"), "method_fqn": mfqn,
            "return_type": _g(r, "returnType", "return_type"),
            "param_types_json": _j(_g(r, "paramTypes", "param_types", [])),
            "param_names_json": _j(_g(r, "paramNames", "param_names", [])),
            "modifiers_json": _j(_g(r, "modifiers", "modifiers", [])),
            "annotations_json": _j(_g(r, "annotations", "annotations", [])),
            "is_constructor": 1 if _g(r, "isConstructor", "is_constructor", False) else 0,
            "source_file": sf,
            "start_line": _line0(_g(r, "startLine", "start_line", 0)),
            "end_line": _line0(_g(r, "endLine", "end_line", 0)),
        })

    fields: list[dict[str, Any]] = []
    for r in _load_jsonl(out_dir / "fields.jsonl"):
        cfqn = _norm_fqn(_g(r, "classFqn", "class_fqn"))
        if not cfqn:
            dropped["fields"] += 1
            continue
        name = _g(r, "fieldName", "field_name")
        fields.append({
            "field_id": _g(r, "fieldId", "field_id"), "lang": "java",
            "class_fqn": cfqn, "field_name": name, "name_lower": name.lower(),
            "field_type": _g(r, "fieldType", "field_type"),
            "modifiers_json": _j(_g(r, "modifiers", "modifiers", [])),
            "annotations_json": _j(_g(r, "annotations", "annotations", [])),
            "source_file": cls_file.get(cfqn, ""),
            "start_line": _line0(_g(r, "startLine", "start_line", 0)),
            "end_line": _line0(_g(r, "endLine", "end_line", 0)),
        })

    calls: list[dict[str, Any]] = []
    for r in _load_jsonl(out_dir / "calls.jsonl"):
        caller = _norm_fqn(_g(r, "callerMethodFqn", "caller_method_fqn"))
        calls.append({
            "call_id": _g(r, "callId", "call_id"), "lang": "java",
            "caller_method_fqn": caller,
            "callee_class_fqn": _norm_fqn(_g(r, "calleeClassFqn", "callee_class_fqn")),
            "callee_method_name": _g(r, "calleeMethodName", "callee_method_name"),
            "callee_signature": _g(r, "calleeSignature", "callee_signature"),
            "callee_method_fqn": _norm_fqn(_g(r, "calleeMethodFqn", "callee_method_fqn")),
            "receiver_type": _g(r, "receiverType", "receiver_type"),
            "dispatch_kind": _g(r, "dispatchKind", "dispatch_kind"),
            "source_file": meth_file.get(caller, ""),
            "line_no": _line0(_g(r, "lineNo", "line_no", 0)),
            "resolved": 1 if _g(r, "resolved", "resolved", False) else 0,
        })

    # HIGH-2(最终 review)+ merge-review 修订: 空 sub 跳过(同 classes/methods/fields 款);
    # 去重 key = (sub, super, source_file) —— 同文件同 (sub,super) 重复行(jar 异常/
    # extends+implements 病理)保首行; **跨文件**同 (sub,super)(重复 FQN 世界合法)共存,
    # schema v3 已是 row_id 代理 PK, 增量按文件删插不再撞约束。
    inh_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in _load_jsonl(out_dir / "inheritance.jsonl"):
        sub = _norm_fqn(_g(r, "subClassFqn", "sub_class_fqn"))
        if not sub:
            dropped["inheritance"] += 1
            continue
        sup = _norm_fqn(_g(r, "superClassFqn", "super_class_fqn"))
        sf = cls_file.get(sub, "")
        key = (sub, sup, sf)
        if key in inh_by_key:
            continue
        inh_by_key[key] = {
            "sub_class_fqn": sub, "lang": "java",
            "super_class_fqn": sup,
            "relation_type": _g(r, "relationType", "relation_type"),
            "source_file": sf,
        }
    inheritance = list(inh_by_key.values())

    references: list[dict[str, Any]] = []
    for r in _load_jsonl(out_dir / "references.jsonl"):
        references.append({
            "lang": "java",
            "source_fqn": _norm_fqn(_g(r, "sourceFqn", "source_fqn")),
            "source_file": _rel(_g(r, "sourceFile", "source_file"), repo_root, rel_stats),
            "target_fqn": _norm_fqn(_g(r, "targetFqn", "target_fqn")),
            "target_kind": _g(r, "targetKind", "target_kind"),
            "ref_kind": _g(r, "refKind", "ref_kind"),
            "line_no": _line0(_g(r, "lineNo", "line_no", 0)),
            # column 不归一: jar 用 JDT cu.getColumnNumber, 本就 0-based(首字符列 0)
            "column_no": int(_g(r, "columnNo", "column_no", 0)),
        })

    files: list[dict[str, Any]] = []
    for r in _load_jsonl(out_dir / "files.jsonl"):
        rel = _rel(_g(r, "path", "path"), repo_root, rel_stats)
        # F2: 真 jar files.jsonl 不带 sha1 字段 -> 磁盘文件存在就补算(增量基准的
        # 数据源); JSONL 若带 sha1 则信 JSONL 不重复算; 文件不存在保持 ""。
        sha1 = _g(r, "sha1", "sha1")
        if not sha1 and rel:
            fp = repo_root / rel
            if fp.is_file():
                sha1 = hashlib.sha1(fp.read_bytes()).hexdigest()
        files.append({
            "file_path": rel, "lang": "java",
            "module": _g(r, "module", "module"),
            "package_name": _g(r, "packageName", "package_name"),
            "sha1": sha1,
        })

    # schema v3 已是代理 PK; 去重 key 含 source_file(同 inheritance 款, 跨文件共存,
    # 同文件重复保首行; v1 此文件预期不存在, 盲区 3)
    tr_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in _load_jsonl(out_dir / "table_refs.jsonl"):
        mfqn = _norm_fqn(_g(r, "methodFqn", "method_fqn"))
        tname = _g(r, "tableName", "table_name")
        tkey = (mfqn, tname, meth_file.get(mfqn, ""))
        if tkey in tr_by_key:
            continue
        tr_by_key[tkey] = {
            "method_fqn": mfqn, "lang": "java",
            "table_name": tname,
            "db_name": _g(r, "dbName", "db_name"),
            "owner": _g(r, "owner", "owner"),
            "ref_kind": _g(r, "refKind", "ref_kind"),
            "source_file": meth_file.get(mfqn, ""),
        }
    table_refs = list(tr_by_key.values())

    if any(dropped.values()):
        logger.warning(
            "dropped records with empty class FQN (local classes): "
            "classes=%d methods=%d fields=%d inheritance=%d",
            dropped["classes"], dropped["methods"], dropped["fields"],
            dropped["inheritance"])
    if rel_stats["count"]:
        logger.warning(
            "paths outside repo_root kept as-is: %d (first sample: %s)",
            rel_stats["count"], rel_stats["first"])

    return {
        "code_files": files, "code_classes": classes, "code_methods": methods,
        "code_fields": fields, "code_calls": calls, "code_inheritance": inheritance,
        "code_references": references, "code_table_refs": table_refs,
    }
