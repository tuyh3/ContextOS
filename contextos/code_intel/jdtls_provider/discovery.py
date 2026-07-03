"""自动探测 JDT LS 运行时来源, 拼出 profile [jdtls_runtime] 路径建议。

动机(2026-07-02 用户指出): jdtls_path/lombok_path/java_home 三个必填路径此前既没文档
讲从哪来, 填错也只有裸"路径不存在"报错。本模块把我们自己踩过的三个定位坑机器化:
平台后缀(darwin-arm64/win32-x64/linux-x64)、jre 下还有一层"JDK版本-平台"目录、
lombok jar 文件名可能带版本号。消费方 = health_check 的 jdtls_runtime 探针(缺路径时
打印现成建议)。只读探测, 不写任何文件(回写 human-gated: 建议由用户自己贴进 profile)。

两条探测支路(spec C1 顺序在探针层, 本模块只提供各支路):
- discover_runtime_bundle: <仓根>/runtime/contextos-runtime 官方 Release 解压布局,
  四件套深校验(C4: launcher jar + 平台 config + lombok + jre/bin/java + java-indexer.jar,
  任一缺 = 未命中不给假 ok), 建议比 VSCode 支路多一行 indexer_jar。
- discover_vscode_jdtls: 本机 VSCode redhat.java 扩展扫描(既有)。
"""
from __future__ import annotations

import re
import sys
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


# --------------------------------------------------------------------- runtime bundle(spec C1/C4)


@dataclass
class DiscoveredRuntime:
    """runtime bundle 命中结果: 比 DiscoveredJdtls 多 indexer_jar(bundle 独有,
    VSCode 扩展没有 java-indexer, spec C2 要求 bundle 建议给出这第四行)。"""

    jdtls_path: str
    lombok_path: str
    java_home: str
    indexer_jar: str
    source: str          # 恒 "runtime-bundle", 供人核对建议来自哪条支路


def _current_platform_config() -> str:
    """当前平台 -> JDT LS config 目录名。key 词形与 SSOT 字典
    (eclipse_jdtls.JDTLS_CONFIG_DIR_BY_PLATFORM)对齐: osx-*/linux-*/win-x64。
    sys.platform=="win32" 对 64 位 Windows 也成立(三平台兼容教训)。"""
    import platform as _pf

    if sys.platform == "win32":
        key = "win-x64"
    elif sys.platform == "darwin":
        key = "osx-arm64" if _pf.machine() == "arm64" else "osx-x64"
    else:
        key = "linux-arm64" if _pf.machine() in ("arm64", "aarch64") else "linux-x64"
    # 函数内 import: eclipse_jdtls 模块大, 别为一个字典把它拖进 discovery 的常规 import 面。
    from contextos.code_intel.jdtls_provider.solidlsp.language_servers.eclipse_jdtls import (
        JDTLS_CONFIG_DIR_BY_PLATFORM,
    )
    return JDTLS_CONFIG_DIR_BY_PLATFORM[key]


def validate_jdtls_layout(jdtls_dir: Path, platform_config: str | None = None) -> str | None:
    """深校验 JDT LS 目录(spec C4): launcher jar + 当前平台 config_* 目录。

    返回 None=通过, str=缺什么(人话原因, 探针直接放进 missing 清单)。动机: 浅
    exists() 假 ok 的两个真实炸点 —— jdtls 目录在但没 plugins/launcher(拷了半截),
    或 bundle 是别的平台的(config_mac_arm vs config_win), 都要等 init 才炸。
    platform_config 参数供测试注入(合成树平台无关), 生产走当前平台。"""
    if not list((jdtls_dir / "plugins").glob("org.eclipse.equinox.launcher_*.jar")):
        return "jdtls/plugins 缺 org.eclipse.equinox.launcher_*.jar"
    cfg = platform_config or _current_platform_config()
    if not (jdtls_dir / cfg).is_dir():
        return f"jdtls 缺当前平台配置目录 {cfg}"
    return None


def discover_runtime_bundle(
    repo: Path | None = None, platform_config: str | None = None
) -> DiscoveredRuntime | None:
    """<repo|cwd>/runtime/contextos-runtime 四件套深校验(spec C1/C4), 全过才命中。

    锚 = cwd(与 projection/paths.indexer_jar 的相对路径约定同款; repo 参数供注入)。
    输出全绝对路径 as_posix 形(spec C2/C3: 相对路径会被 validate_profile 拒,
    且 indexer_jar 挂 cwd 解析会随启动目录漂移; 绝对路径两处都稳)。
    任一件缺 = None(探针 fall through 下一支路), 不做联网。"""
    root = ((repo or Path.cwd()) / "runtime" / "contextos-runtime").resolve()
    jdtls = root / "jdtls"
    jre = root / "jre"
    lombok = root / "lombok.jar"
    indexer = root / "java-indexer.jar"
    java = jre / "bin" / ("java.exe" if sys.platform == "win32" else "java")
    if not (jdtls.is_dir() and lombok.is_file() and indexer.is_file() and java.is_file()):
        return None
    if validate_jdtls_layout(jdtls, platform_config) is not None:
        return None
    return DiscoveredRuntime(
        jdtls_path=jdtls.as_posix(),
        lombok_path=lombok.as_posix(),
        java_home=jre.as_posix(),
        indexer_jar=indexer.as_posix(),
        source="runtime-bundle",
    )
