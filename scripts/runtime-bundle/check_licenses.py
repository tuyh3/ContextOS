#!/usr/bin/env python3
"""THIRD-PARTY.txt(license-maven-plugin add-third-party 产物)对 allowlist 的 fail-closed 校验,
外加 licenses.xml(download-licenses 产物)的 license 全文台账 fail-closed 校验。

被 scripts/check-indexer-licenses.sh 调用; 抽成独立 py 是为了 pytest 可直接测
(contextos/tests/test_license_allowlist_check.py 三态 fixture)。

纪律(本闸存在意义): 不认识的行绝不静默跳过 —— 报告格式一变, "continue 跳过"
会把所有依赖放过(假绿)。因此:
- 可跳过的只有显式声明的非记录行: 空行 / "Lists of N third-party dependencies." 头部
  / "The project has no dependencies."(后者仍会因 0 记录而红)
- 其余每行必须解析成依赖记录, 解析不出 = 立即红
- 一条记录可有多个 license 括号组(双许可): any-of 取其一在 allowlist 即放行
- 任一 license 含 "unknown"(不分大小写)必红, 优先于 any-of
- 0 条依赖记录必红(shade fat-jar 工程不可能 0 依赖)
- 头部声明数 N 与实际解析出的记录数不一致必红(防报告被截断)

正则以真跑报告校准(2026-07-03, license-maven-plugin 2.4.0, 19 条依赖), 样本:
     (The Apache Software License, Version 2.0) Jackson-core (com.fasterxml.jackson.core:jackson-core:2.13.5 - https://github.com/FasterXML/jackson-core)
     (Apache-2.0) (LGPL-2.1-or-later) Java Native Access (net.java.dev.jna:jna:5.18.1 - https://github.com/java-native-access/jna)

---

第二道闸: licenses.xml 全文台账校验(治"license 全文部分下载失败被静默放行"缺陷,
2026-07-05 诊断)。download-licenses goal 的 errorRemedy=warn 会在单个 license 全文下载
失败(如 eclipse.org 路由问题)时只告警不报错, 生成的 licenses.xml 里该依赖的
<license> 条目就没有 <file> 元素, 而调用方(check-indexer-licenses.sh 旧版)只查
licenses/ 目录非空就放行 —— 三层叠加, 半成品(缺全文)也能出包。

修法: 对 licenses.xml 逐 dependency 断言"至少一个 license 名落在 allowlist 内的条目
带 <file> 且该文件在 licenses/ 目录下真实存在"。语义与 THIRD-PARTY.txt 的 any-of/
双许可校验同构 —— 双许可依赖(如 JNA 的 LGPL 不在 allowlist)只要求 allowlist 内那侧
(Apache-2.0)有全文, 不强求 LGPL 也有, 零破坏既有语义。

用法:
  逐行校验:  python3 check_licenses.py <THIRD-PARTY.txt> <license-allowlist.txt>
  全文台账:  python3 check_licenses.py --texts-manifest <licenses.xml> <license-allowlist.txt>
退出码: 0 = 全部放行; 1 = 任一红(原因逐条打到 stderr)。
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# 头部行: "Lists of 19 third-party dependencies."(模板固定复数, 保险起见也认单数)
HEADER_RE = re.compile(r"^Lists of (\d+) third-party dependenc(?:y|ies)\.$")
# 空工程行(显式认识它, 但 0 记录仍然红)
NO_DEPS_RE = re.compile(r"^The project has no dependencies\.$")
# 依赖记录行: 缩进 + 1..n 个 (license) 组 + 名称 + (group:artifact:version - url)
RECORD_RE = re.compile(
    r"^\s+"
    r"(?P<lics>\([^()]*\)(?:\s*\([^()]*\))*)"          # license 括号组(可多个)
    r"\s+(?P<name>\S.*?)"                               # 依赖显示名
    r"\s+\((?P<coord>[^()\s]+:[^()\s]+:[^()\s]+) - (?P<url>[^()]*)\)\s*$"
)
LIC_TOKEN_RE = re.compile(r"\(([^()]*)\)")


@dataclass
class CheckResult:
    ok: bool
    dep_count: int
    problems: list[str] = field(default_factory=list)


def parse_allowlist(text: str) -> set[str]:
    """一行一个 license 名; 空行与 # 注释行跳过; 首尾空白剥掉。"""
    allowed: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        allowed.add(s)
    return allowed


def check_report(report_text: str, allowed: set[str]) -> CheckResult:
    problems: list[str] = []
    declared: int | None = None
    dep_count = 0

    for lineno, raw in enumerate(report_text.splitlines(), start=1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue  # 空行: 显式声明可跳过
        m = HEADER_RE.match(line.strip())
        if m:
            if declared is not None:
                problems.append(f"line {lineno}: 出现第二个头部行, 报告结构异常: {line.strip()!r}")
            declared = int(m.group(1))
            continue
        if NO_DEPS_RE.match(line.strip()):
            declared = 0
            continue
        m = RECORD_RE.match(line)
        if m is None:
            # fail-closed: 解析不出的行绝不静默跳过
            problems.append(f"line {lineno}: 无法解析为依赖记录(格式变了? 重新校准 RECORD_RE): {line.strip()!r}")
            continue
        dep_count += 1
        lics = [t.strip() for t in LIC_TOKEN_RE.findall(m.group("lics"))]
        coord = m.group("coord")
        if any(not t for t in lics):
            problems.append(f"line {lineno}: {coord} 出现空 license 括号组")
            continue
        if any("unknown" in t.lower() for t in lics):
            problems.append(f"line {lineno}: {coord} license 含 Unknown(必红, 不参与 any-of): {lics}")
            continue
        if not any(t in allowed for t in lics):
            problems.append(
                f"line {lineno}: {coord} 无任一 license 在 allowlist 内: {lics}"
                "(逐条核实真实 license; 属实可再分发 -> 加 allowlist 带注释; 否则 STOP 上报)"
            )

    if declared is None:
        problems.append("报告缺 'Lists of N third-party dependencies.' 头部行(格式变了? 重新校准 HEADER_RE)")
    elif declared != dep_count:
        problems.append(f"头部声明 {declared} 条依赖, 实际解析出 {dep_count} 条(报告被截断或格式漂移)")
    if dep_count == 0:
        problems.append("0 条依赖记录(java-indexer 是 shade fat-jar 工程, 不可能 0 依赖)")

    return CheckResult(ok=not problems, dep_count=dep_count, problems=problems)


def check_texts_manifest(xml_text: str, allowed: set[str], licenses_dir: Path) -> CheckResult:
    """licenses.xml(download-licenses 产物)逐 dependency 断言:

    至少一个 license 条目同时满足: 名字在 allowlist 内 且 带 <file> 且该文件
    在 licenses_dir 下真实存在。双许可依赖(如 JNA)只要 allowlist 内那侧齐全文即可,
    不强求另一侧(如 LGPL, 本就不在 allowlist)也有 —— 与 THIRD-PARTY.txt 的
    any-of 语义同构, 零破坏既有采用侧收录方式。

    fail-closed 纪律与 check_report 一致: XML 解析不出 / dependency 无 licenses 子结构
    一律红, 不静默跳过。
    """
    problems: list[str] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return CheckResult(ok=False, dep_count=0, problems=[f"licenses.xml 解析失败(XML 格式错误): {exc}"])

    dependencies = root.findall("./dependencies/dependency")
    dep_count = len(dependencies)

    for dep in dependencies:
        group_id = (dep.findtext("groupId") or "").strip()
        artifact_id = (dep.findtext("artifactId") or "").strip()
        version = (dep.findtext("version") or "").strip()
        coord = f"{group_id}:{artifact_id}:{version}"

        licenses = dep.findall("./licenses/license")
        if not licenses:
            problems.append(f"{coord}: 无 <license> 条目(licenses.xml 结构异常, 重新核实)")
            continue

        found_ok = False
        names: list[str] = []
        for lic in licenses:
            name = (lic.findtext("name") or "").strip()
            names.append(name or "(空名)")
            if name not in allowed:
                continue
            file_name = (lic.findtext("file") or "").strip()
            if not file_name:
                continue
            if (licenses_dir / file_name).is_file():
                found_ok = True
                break

        if not found_ok:
            problems.append(
                f"{coord}: 无任一 allowlist 内 license 带真实存在的全文文件, "
                f"license 名={names}(license 全文下载失败被静默放行? 重跑 download-licenses "
                "或核实本机到上游站点路由)"
            )

    if dep_count == 0:
        problems.append("licenses.xml 里 0 条 dependency(java-indexer 是 shade fat-jar 工程, 不可能 0 依赖)")

    return CheckResult(ok=not problems, dep_count=dep_count, problems=problems)


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--texts-manifest":
        if len(argv) != 4:
            print(f"用法: {argv[0]} --texts-manifest <licenses.xml> <license-allowlist.txt>", file=sys.stderr)
            return 2
        xml_path, allowlist_path = Path(argv[2]), Path(argv[3])
        for p in (xml_path, allowlist_path):
            if not p.is_file():
                print(f"文件不存在: {p}", file=sys.stderr)
                return 1
        allowed = parse_allowlist(allowlist_path.read_text(encoding="utf-8"))
        if not allowed:
            print(f"allowlist 为空(全注释?): {allowlist_path}", file=sys.stderr)
            return 1
        licenses_dir = xml_path.parent / "licenses"
        result = check_texts_manifest(xml_path.read_text(encoding="utf-8"), allowed, licenses_dir)
        if not result.ok:
            print("license 全文台账校验 FAIL(fail-closed):", file=sys.stderr)
            for prob in result.problems:
                print(f"  - {prob}", file=sys.stderr)
            return 1
        print(f"license 全文台账 OK ({result.dep_count} 条依赖)")
        return 0

    if len(argv) != 3:
        print(f"用法: {argv[0]} <THIRD-PARTY.txt> <license-allowlist.txt>", file=sys.stderr)
        print(f"      {argv[0]} --texts-manifest <licenses.xml> <license-allowlist.txt>", file=sys.stderr)
        return 2
    report_path, allowlist_path = Path(argv[1]), Path(argv[2])
    for p in (report_path, allowlist_path):
        if not p.is_file():
            print(f"文件不存在: {p}", file=sys.stderr)
            return 1
    allowed = parse_allowlist(allowlist_path.read_text(encoding="utf-8"))
    if not allowed:
        print(f"allowlist 为空(全注释?): {allowlist_path}", file=sys.stderr)
        return 1
    result = check_report(report_path.read_text(encoding="utf-8"), allowed)
    if not result.ok:
        print("license allowlist 校验 FAIL(fail-closed):", file=sys.stderr)
        for prob in result.problems:
            print(f"  - {prob}", file=sys.stderr)
        return 1
    print(f"license allowlist OK ({result.dep_count} 条依赖)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
