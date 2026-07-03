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
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
