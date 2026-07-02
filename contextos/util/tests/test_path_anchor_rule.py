"""路径锚点归一规则的结构性证明(Windows 阶段2 spec 附录B/§7)。

设计思路: 4 处生产代码(jsonl_load._rel / incremental._scan_source_roots /
source_scan.scan_sources / watcher._normalize_event_path)都用同一套规则——
仓内 = relative_to(root).as_posix(); 仓外 = 原路径.as_posix()。mac 上无法
真跑 Windows 路径(Path 是平台原生类, 反斜杠不会出现), 所以本文件拆两层证明:
  1. 真 Path + POSIX 输入: 证 mac/Linux 上此规则与旧的 str(...) 恒等(零回归)。
  2. PureWindowsPath 输入(纯字符串路径运算, 不碰文件系统, mac 可跑): 证这套
     "relative_to().as_posix()" 规则本身对 Windows 风格路径(反斜杠 + 盘符)
     转换正确 —— 这是规则级证明, 不是调用具体生产函数(那些函数内部用平台
     原生 Path, 其真 Windows 行为只能在真机/Windows CI 验, 见 §7)。

评分标准: 两层各 2 个断言(in-repo 相对 / out-of-repo 绝对), 全通过即规则成立。
"""
from __future__ import annotations

import sys
from pathlib import Path, PureWindowsPath

import pytest


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="本测证 POSIX 上 as_posix()==str() 的零回归恒等; Windows 原生 Path.str() 用反斜杠, "
    "该恒等本就不成立(Windows 风格转换规则由 test_windows_style_conversion_rule 覆盖)。",
)
def test_posix_real_path_zero_regression():
    """真 Path, POSIX 输入: as_posix() 与旧 str() 恒等(mac/Linux 零回归)。"""
    root = Path("/repo")
    in_repo = Path("/repo/src/A.java")
    assert in_repo.relative_to(root).as_posix() == str(in_repo.relative_to(root))
    assert in_repo.relative_to(root).as_posix() == "src/A.java"

    out_of_repo = Path("/external/X.java")
    assert out_of_repo.as_posix() == str(out_of_repo)
    assert out_of_repo.as_posix() == "/external/X.java"


def test_windows_style_conversion_rule():
    """PureWindowsPath 模拟(mac 可跑, 不碰真文件系统): 证转换规则对 Windows
    风格路径(反斜杠 + 盘符)成立 —— 这是 4 处生产代码修改依赖的核心规则。"""
    root = PureWindowsPath(r"C:\repo")
    in_repo = PureWindowsPath(r"C:\repo\src\A.java")
    assert in_repo.relative_to(root).as_posix() == "src/A.java"

    out_of_repo = PureWindowsPath(r"D:\external\X.java")
    assert out_of_repo.as_posix() == "D:/external/X.java"
