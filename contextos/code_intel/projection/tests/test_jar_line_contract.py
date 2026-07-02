"""真 jar 行号契约(HIGH-1 防再漂移): vendored java-indexer 吐 1-based 行号
(JDT cu.getLineNumber), loader 必须归一为 0-based(LSP/投影契约)。

合成 fixture 只能锁 "loader 对输入减一", 锁不住 "jar 输出确实是 1-based" 这半边;
本测试真跑 jar: tmp 写已知内容 Java 文件(class 声明精确在 0-based 第 1 行)->
run_indexer -> load_all_rows -> 断言 0-based 行号。jar/JDK8 不在则 skip(CI 离线),
本机必须真跑非 skip。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextos.code_intel.projection.indexer_runner import run_indexer
from contextos.code_intel.projection.jsonl_load import load_all_rows

_REPO = Path(__file__).resolve().parents[4]
JAR = _REPO / "vendor" / "java-indexer" / "target" / "java-indexer-1.0.0.jar"
JAVA8_HOME = Path("/Library/Java/JavaVirtualMachines/jdk1.8.0_321.jdk/Contents/Home")
JAVA8 = JAVA8_HOME / "bin" / "java"


@pytest.mark.skipif(not JAR.exists() or not JAVA8.exists(),
                    reason="vendored jar/JDK8 unavailable")
def test_real_jar_lines_normalized_to_0_based(tmp_path):
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    # 0-based 行号: 0=package, 1=class 声明, 2=method 声明, 4=method 尾, 5=class 尾
    (src / "ContractProbe.java").write_text(
        "package com.acme;\n"
        "public class ContractProbe {\n"
        "    public int go() {\n"
        "        return 1;\n"
        "    }\n"
        "}\n", encoding="utf-8")
    ctx = tmp_path / "build_context.json"
    ctx.write_text(json.dumps({
        "java_version": "1.8",
        "modules": [{"name": "m", "source_roots": [str(src)],
                     "classpath_entries": [], "encoding": "UTF-8"}],
    }), encoding="utf-8")
    out = tmp_path / "out"

    run_indexer(java_home=str(JAVA8_HOME), jar=JAR, xmx="1g",
                ctx_file=ctx, out_dir=out, timeout_seconds=300)
    rows = load_all_rows(out, repo_root=repo)

    cls = next(c for c in rows["code_classes"]
               if c["class_fqn"] == "com.acme.ContractProbe")
    assert cls["start_line"] == 1            # jar 原始 2(1-based)-> 0-based 1
    assert cls["end_line"] == 5
    m = next(m for m in rows["code_methods"] if m["method_name"] == "go")
    assert (m["start_line"], m["end_line"]) == (2, 4)
