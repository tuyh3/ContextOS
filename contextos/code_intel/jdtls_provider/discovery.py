"""自动探测本机 VSCode redhat.java 扩展, 拼出 profile [jdtls_runtime] 三路径建议。

动机(2026-07-02 用户指出): jdtls_path/lombok_path/java_home 三个必填路径此前既没文档
讲从哪来, 填错也只有裸"路径不存在"报错。本模块把我们自己踩过的三个定位坑机器化:
平台后缀(darwin-arm64/win32-x64/linux-x64)、jre 下还有一层"JDK版本-平台"目录、
lombok jar 文件名可能带版本号。消费方 = health_check 的 jdtls_runtime 探针(缺路径时
打印现成建议)。只读探测, 不写任何文件(回写 human-gated: 建议由用户自己贴进 profile)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_EXT_VERSION_RE = re.compile(r"^redhat\.java-(\d+)\.(\d+)\.(\d+)")


@dataclass
class DiscoveredJdtls:
    jdtls_path: str
    lombok_path: str
    java_home: str
    source: str  # 探到的扩展目录名, 供人核对(如 redhat.java-1.53.0-darwin-arm64)


def _version_key(ext_dir: Path) -> tuple[int, int, int]:
    m = _EXT_VERSION_RE.match(ext_dir.name)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def _find_jre_home(jre_root: Path) -> Path | None:
    """jre/ 下钻一层版本目录(如 jre/21.0.10-win32-x86_64), 认"bin/java 或 bin/java.exe
    存在"为 JRE home; 个别布局 jre/ 自己就是 home, 先查自身再钻子目录(取名字最大=最新)。"""
    def _is_home(d: Path) -> bool:
        return (d / "bin" / "java").exists() or (d / "bin" / "java.exe").exists()

    if _is_home(jre_root):
        return jre_root
    if not jre_root.is_dir():
        return None
    subs = sorted((d for d in jre_root.iterdir() if d.is_dir() and _is_home(d)),
                  key=lambda d: d.name)
    return subs[-1] if subs else None


def discover_vscode_jdtls(home: Path | None = None) -> DiscoveredJdtls | None:
    """扫 <home>/.vscode/extensions/redhat.java-*, 从最高版本往下找第一个三件齐全的。

    路径一律 as_posix() 输出: 建议文本会被用户贴进 TOML, Windows 原生反斜杠在双引号
    串里是转义炸弹(范本头三条纪律), 正斜杠三平台通吃。home 参数供测试注入。
    探不到(没装 VSCode / 没装 redhat.java / 目录残缺)返回 None, 由调用方给安装指引。
    """
    base = (home or Path.home()) / ".vscode" / "extensions"
    if not base.is_dir():
        return None
    candidates = sorted(
        (d for d in base.glob("redhat.java-*") if d.is_dir()),
        key=_version_key, reverse=True,
    )
    for ext in candidates:
        server = ext / "server"
        lomboks = sorted((ext / "lombok").glob("lombok*.jar"))
        jre_home = _find_jre_home(ext / "jre")
        if server.is_dir() and lomboks and jre_home is not None:
            return DiscoveredJdtls(
                jdtls_path=server.as_posix(),
                lombok_path=lomboks[-1].as_posix(),
                java_home=jre_home.as_posix(),
                source=ext.name,
            )
    return None
