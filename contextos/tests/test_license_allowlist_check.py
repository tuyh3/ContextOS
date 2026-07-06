"""scripts/runtime-bundle/check_licenses.py 三态 fixture 单测(runtime-bundle Task 3)。

设计思路:
- 被测对象是 license fail-closed 闸的解析+判定核心(bash 壳 check-indexer-licenses.sh 只做
  mvn 报告生成与转发)。抽成独立 py 正是为了这里能直接测。
- 三态 fixture(spec 要求): ① 合法报告 -> 绿; ② 含 Unknown license -> 红;
  ③ 含格式外未知行 -> 红(fail-closed 存在意义: 格式漂移绝不能静默放行成假绿)。
- 另盖闸的其余红态: 不在 allowlist / 头部计数与记录数不符(截断) / 0 记录 / 缺头部。
- fixture 全部用合成中性值(com.example 坐标 + 虚构 license 名), 项目纪律: tests 会公开,
  不掺客户词; 报告行格式则严格复刻 license-maven-plugin 2.4.0 真产物(缩进 5 空格,
  括号 license 组 + 显示名 + "(g:a:v - url)")。

评分标准: 绿态 ok=True 且 dep_count 精确; 每个红态 ok=False 且 problems 里能定位到原因
(不只测 exit 非 0, 防"红了但红错原因"假通过)。

脚本逻辑: importlib 从 scripts/runtime-bundle/ 按文件路径加载(该目录不是包);
真仓 allowlist 另测一条覆盖校准词形, 防 allowlist 被误删导致真报告转红。

---

第二组: check_texts_manifest(licenses.xml 全文台账闸, 2026-07-05 治"license 全文部分
下载失败被静默放行"缺陷新增)。三态 fixture 同款纪律:
- 好台账(每 dependency 的 allowlist 内 license 都带真实存在的 <file>) -> 绿。
- EPL 全缺 <file>(本次真实缺陷态复现, 见 vendor/java-indexer 生成的坏 licenses.xml)
  -> 红, problems 定位到具体缺失的依赖坐标。
- 双许可(Apache 有 file + LGPL 无 file 且 LGPL 不在 allowlist) -> 绿(any-of 语义
  与 THIRD-PARTY.txt 校验同构, 采用侧收录即可, 不强求另一侧也有全文)。
用 tmp_path 落合成 licenses.xml + licenses/ 目录(<file> 只是 basename, 真实性靠
"文件在 licenses_dir 下存在"断言, 必须真落盘才测得出"缺文件"这一态)。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "runtime-bundle" / "check_licenses.py"
REAL_ALLOWLIST = REPO_ROOT / "scripts" / "runtime-bundle" / "license-allowlist.txt"

_spec = importlib.util.spec_from_file_location("check_licenses", CHECKER_PATH)
assert _spec is not None and _spec.loader is not None
check_licenses = importlib.util.module_from_spec(_spec)
# dataclasses 解析 str 注解时按 __module__ 查 sys.modules, 必须先注册再 exec
sys.modules["check_licenses"] = check_licenses
_spec.loader.exec_module(check_licenses)

# 合成中性 allowlist(虚构 license 名, 与真 allowlist 解耦)
ALLOW = {"Example Permissive License", "Sample Open License 2.0"}

VALID_REPORT = """
Lists of 3 third-party dependencies.
     (Example Permissive License) Alpha Widget (com.example:alpha-widget:1.0.0 - https://example.com/alpha)
     (Sample Open License 2.0) Beta Toolkit (com.example:beta-toolkit:2.1.0 - https://example.com/beta)
     (Example Permissive License) (Nonfree Sample License) Gamma Lib (com.example:gamma-lib:0.9.0 - https://example.com/gamma)
"""


def test_valid_report_passes() -> None:
    """态①: 合法报告 -> 绿; 双许可 any-of(gamma-lib 有一个在单)放行。"""
    result = check_licenses.check_report(VALID_REPORT, ALLOW)
    assert result.ok, result.problems
    assert result.dep_count == 3
    assert result.problems == []


def test_unknown_license_fails() -> None:
    """态②: 含 Unknown license -> 红; 且 Unknown 优先于 any-of(另一个在单也不放行)。"""
    report = """
Lists of 2 third-party dependencies.
     (Unknown license) Alpha Widget (com.example:alpha-widget:1.0.0 - https://example.com/alpha)
     (Example Permissive License) (unknown) Beta Toolkit (com.example:beta-toolkit:2.1.0 - https://example.com/beta)
"""
    result = check_licenses.check_report(report, ALLOW)
    assert not result.ok
    unknown_problems = [p for p in result.problems if "Unknown" in p]
    assert len(unknown_problems) == 2  # 两条都因 Unknown 红, 第二条不被 any-of 洗绿


def test_unrecognized_line_fails() -> None:
    """态③: 格式外未知行 -> 红(fail-closed: 绝不静默跳过)。"""
    report = VALID_REPORT + "some stray line that is not a record\n"
    result = check_licenses.check_report(report, ALLOW)
    assert not result.ok
    assert any("无法解析" in p for p in result.problems)


def test_license_not_in_allowlist_fails() -> None:
    report = """
Lists of 1 third-party dependencies.
     (Nonfree Sample License) Alpha Widget (com.example:alpha-widget:1.0.0 - https://example.com/alpha)
"""
    result = check_licenses.check_report(report, ALLOW)
    assert not result.ok
    assert any("无任一 license 在 allowlist" in p for p in result.problems)


def test_header_count_mismatch_fails() -> None:
    """头部声明 5 条实际 3 条 -> 红(防报告截断假绿)。"""
    report = VALID_REPORT.replace("Lists of 3", "Lists of 5")
    result = check_licenses.check_report(report, ALLOW)
    assert not result.ok
    assert any("头部声明 5 条" in p for p in result.problems)


def test_zero_records_fails() -> None:
    """'The project has no dependencies.' 被显式认识, 但 0 记录仍红(shade 工程不可能 0 依赖)。"""
    result = check_licenses.check_report("\nThe project has no dependencies.\n", ALLOW)
    assert not result.ok
    assert any("0 条依赖记录" in p for p in result.problems)
    # 显式认识 = 不产生 "无法解析" 类问题
    assert not any("无法解析" in p for p in result.problems)


def test_missing_header_fails() -> None:
    report = "     (Example Permissive License) Alpha Widget (com.example:alpha-widget:1.0.0 - https://example.com/alpha)\n"
    result = check_licenses.check_report(report, ALLOW)
    assert not result.ok
    assert any("缺" in p and "头部" in p for p in result.problems)


def test_real_allowlist_covers_calibrated_forms() -> None:
    """真仓 allowlist 必须含 2026-07-03 真报告实测的 4 个词形(误删会让真报告转红)。"""
    allowed = check_licenses.parse_allowlist(REAL_ALLOWLIST.read_text(encoding="utf-8"))
    for form in (
        "The Apache Software License, Version 2.0",
        "Apache-2.0",
        "Eclipse Public License - v 2.0",
        "EPL-2.0",
    ):
        assert form in allowed, f"allowlist 缺实测词形: {form}"
    # LGPL-2.1-or-later 刻意不在单(JNA 走 any-of Apache-2.0), 防有人顺手加上
    assert "LGPL-2.1-or-later" not in allowed


# ---------------------------------------------------------------------------
# check_texts_manifest(licenses.xml 全文台账闸)三态 fixture
# ---------------------------------------------------------------------------

TEXTS_ALLOW = {"The Apache Software License, Version 2.0", "Apache-2.0", "Eclipse Public License - v 2.0", "EPL-2.0"}

APACHE_LICENSE_FILE = "the apache software license, version 2.0 - license-2.0.txt"
EPL_LICENSE_FILE = "eclipse public license - v 2.0 - epl-2.0.html"


def _dep_xml(group_id: str, artifact_id: str, version: str, licenses_xml: str) -> str:
    return f"""
    <dependency>
      <groupId>{group_id}</groupId>
      <artifactId>{artifact_id}</artifactId>
      <version>{version}</version>
      <licenses>
        {licenses_xml}
      </licenses>
    </dependency>"""


def _wrap(deps_xml: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<licenseSummary>
  <dependencies>
{deps_xml}
  </dependencies>
</licenseSummary>
"""


def test_texts_manifest_good_ledger_passes(tmp_path: Path) -> None:
    """态①: 每 dependency 的 allowlist 内 license 都带真实存在的 <file> -> 绿。"""
    licenses_dir = tmp_path / "licenses"
    licenses_dir.mkdir()
    (licenses_dir / APACHE_LICENSE_FILE).write_text("Apache License text", encoding="utf-8")
    (licenses_dir / EPL_LICENSE_FILE).write_text("<html>EPL text</html>", encoding="utf-8")

    xml_text = _wrap(
        _dep_xml(
            "com.fasterxml.jackson.core",
            "jackson-core",
            "2.13.5",
            f"""<license>
          <name>The Apache Software License, Version 2.0</name>
          <file>{APACHE_LICENSE_FILE}</file>
        </license>""",
        )
        + _dep_xml(
            "org.eclipse.platform",
            "org.eclipse.core.runtime",
            "3.17.0",
            f"""<license>
          <name>Eclipse Public License - v 2.0</name>
          <file>{EPL_LICENSE_FILE}</file>
        </license>""",
        )
    )

    result = check_licenses.check_texts_manifest(xml_text, TEXTS_ALLOW, licenses_dir)
    assert result.ok, result.problems
    assert result.dep_count == 2
    assert result.problems == []


def test_texts_manifest_missing_epl_files_fails(tmp_path: Path) -> None:
    """态②(本次真实缺陷态复现): EPL 依赖全缺 <file> -> 红, 定位到具体依赖坐标。"""
    licenses_dir = tmp_path / "licenses"
    licenses_dir.mkdir()
    (licenses_dir / APACHE_LICENSE_FILE).write_text("Apache License text", encoding="utf-8")
    # 故意不落盘 EPL 全文, 且 XML 里 EPL license 条目本身就没有 <file> 元素
    # (复现 download-licenses errorRemedy=warn 吞错后的真实产物形态)

    xml_text = _wrap(
        _dep_xml(
            "com.fasterxml.jackson.core",
            "jackson-core",
            "2.13.5",
            f"""<license>
          <name>The Apache Software License, Version 2.0</name>
          <file>{APACHE_LICENSE_FILE}</file>
        </license>""",
        )
        + _dep_xml(
            "org.eclipse.platform",
            "org.eclipse.core.runtime",
            "3.17.0",
            """<license>
          <name>Eclipse Public License - v 2.0</name>
        </license>""",
        )
        + _dep_xml(
            "org.eclipse.platform",
            "org.eclipse.osgi",
            "3.24.200",
            """<license>
          <name>EPL-2.0</name>
        </license>""",
        )
    )

    result = check_licenses.check_texts_manifest(xml_text, TEXTS_ALLOW, licenses_dir)
    assert not result.ok
    assert result.dep_count == 3
    problem_text = "\n".join(result.problems)
    assert "org.eclipse.platform:org.eclipse.core.runtime:3.17.0" in problem_text
    assert "org.eclipse.platform:org.eclipse.osgi:3.24.200" in problem_text
    # 好的 jackson-core 那条不该被牵连报红
    assert "jackson-core" not in problem_text


def test_texts_manifest_dual_license_any_of_passes(tmp_path: Path) -> None:
    """态③: 双许可(Apache 有 file + LGPL 无 file 且 LGPL 不在 allowlist)-> 绿,
    any-of 语义与 THIRD-PARTY.txt 校验同构, 采用侧(Apache)收录即可。"""
    licenses_dir = tmp_path / "licenses"
    licenses_dir.mkdir()
    (licenses_dir / APACHE_LICENSE_FILE).write_text("Apache License text", encoding="utf-8")

    xml_text = _wrap(
        _dep_xml(
            "net.java.dev.jna",
            "jna",
            "5.18.1",
            f"""<license>
          <name>LGPL-2.1-or-later</name>
        </license>
        <license>
          <name>The Apache Software License, Version 2.0</name>
          <file>{APACHE_LICENSE_FILE}</file>
        </license>""",
        )
    )

    result = check_licenses.check_texts_manifest(xml_text, TEXTS_ALLOW, licenses_dir)
    assert result.ok, result.problems
    assert result.dep_count == 1
    assert result.problems == []


def test_texts_manifest_file_element_present_but_not_on_disk_fails(tmp_path: Path) -> None:
    """<file> 元素声明了但磁盘上真实不存在(部分下载失败但 XML 仍写了文件名的边缘情形)-> 红。"""
    licenses_dir = tmp_path / "licenses"
    licenses_dir.mkdir()
    # 故意不落盘对应文件

    xml_text = _wrap(
        _dep_xml(
            "org.eclipse.platform",
            "org.eclipse.osgi",
            "3.24.200",
            f"""<license>
          <name>EPL-2.0</name>
          <file>{EPL_LICENSE_FILE}</file>
        </license>""",
        )
    )

    result = check_licenses.check_texts_manifest(xml_text, TEXTS_ALLOW, licenses_dir)
    assert not result.ok
    assert any("org.eclipse.platform:org.eclipse.osgi:3.24.200" in p for p in result.problems)


def test_texts_manifest_zero_dependencies_fails(tmp_path: Path) -> None:
    licenses_dir = tmp_path / "licenses"
    licenses_dir.mkdir()
    xml_text = _wrap("")
    result = check_licenses.check_texts_manifest(xml_text, TEXTS_ALLOW, licenses_dir)
    assert not result.ok
    assert any("0 条 dependency" in p for p in result.problems)


def test_texts_manifest_malformed_xml_fails(tmp_path: Path) -> None:
    licenses_dir = tmp_path / "licenses"
    licenses_dir.mkdir()
    result = check_licenses.check_texts_manifest("<not><valid xml", TEXTS_ALLOW, licenses_dir)
    assert not result.ok
    assert any("解析失败" in p for p in result.problems)


def test_texts_manifest_real_bad_fixture_reproduces_defect() -> None:
    """本次真实缺陷态复现: 本 worktree 的坏 licenses.xml(14 个 EPL 依赖全缺 <file>)
    对新闸跑必须红。用真实仓内文件而非合成 fixture, 确保闸真的能抓到本次 release-blocking gap。
    """
    repo_root = Path(__file__).resolve().parents[2]
    bad_xml = repo_root / "vendor" / "java-indexer" / "target" / "generated-resources" / "licenses.xml"
    if not bad_xml.is_file():
        pytest.skip("本机未跑过 download-licenses, 没有 licenses.xml 产物可复现")
    allowed = check_licenses.parse_allowlist(REAL_ALLOWLIST.read_text(encoding="utf-8"))
    licenses_dir = bad_xml.parent / "licenses"
    result = check_licenses.check_texts_manifest(bad_xml.read_text(encoding="utf-8"), allowed, licenses_dir)
    # 这份是本次诊断出的坏产物(14 EPL 依赖 0 file), 断言必须能抓到 —— 若这条测试意外变绿,
    # 说明本机产物已被重新生成成好台账, 不代表闸失效(见 check_texts_manifest 合成态测试)。
    if result.ok:
        pytest.skip("本机 licenses.xml 已是好台账(重新跑过 download-licenses?), 缺陷态无法复现")
    problem_text = "\n".join(result.problems)
    assert "org.eclipse" in problem_text
