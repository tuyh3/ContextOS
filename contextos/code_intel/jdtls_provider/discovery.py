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

import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

# resolve_effective_runtime 每个消费方(from_profile / paths.indexer_jar 等)各自
# 调一次 resolver: init 一次跑就打 4 行同款日志, watcher 长跑周期性调用更是重复刷屏。
# 去重键含消息原文(路径/原因都在里面), 同键第二次起降级 debug; 配置一变消息
# (路径/原因)跟着变 -> 键不同 -> 按原级别再报一次, 不是"进程级永不再报"。
_logged_once: set[str] = set()


def _log_dedup(logger_fn, key: str, msg: str, *args: object) -> None:
    """key 首次出现按 logger_fn(通常 log.info/log.warning)原级别打, 重复降 log.debug。"""
    if key in _logged_once:
        log.debug(msg, *args)
    else:
        _logged_once.add(key)
        logger_fn(msg, *args)


RuntimeSource = Literal["profile", "runtime-bundle", "profile-unverified"]

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


# --------------------------------------------------------------------- 生效运行时 resolver(spec A4/A5/A11)


def validate_profile_runtime_paths(
    r: Any, platform_config: str | None = None,
) -> list[str]:
    """profile [jdtls_runtime] 三路径深校验(浅 exists + spec C4 三条腿), 与 health
    探针同一把尺(meta._deep_validate_profile_runtime 迁入, 那边改一行委托)。
    返回缺陷清单, 空 = 有效。

    r: 鸭子类型, 期望有 jdtls_path / lombok_path / java_home 三个 str 属性
    (生产传 profile.jdtls_runtime, 测试传轻量 stub, 故不用 dataclass/isinstance 收窄)。

    浅/深文案分流(runbook 排障契约): 路径本身不存在 -> 无标记的浅原因(常见笔误,
    一眼看出是路径错); 路径存在但内容/结构不对 -> 带"(深校验)"的原因(照抄半截拷贝
    才会撞见, 提示需要再往下查一层)。"""
    problems: list[str] = []
    jdtls = Path(r.jdtls_path).expanduser()
    lombok = Path(r.lombok_path).expanduser()
    jh = Path(r.java_home).expanduser()
    java_name = "java.exe" if sys.platform == "win32" else "java"
    java = jh / "bin" / java_name
    if not jdtls.is_dir():
        problems.append("jdtls_path 不是目录")
    else:
        reason = validate_jdtls_layout(jdtls, platform_config)
        if reason is not None:
            problems.append(f"{reason}(深校验)")
    if not lombok.is_file():
        if not lombok.exists():
            problems.append("lombok_path 不存在")
        else:
            problems.append("lombok_path 不是文件(深校验)")
    if not jh.is_dir():
        problems.append("java_home 不存在")
    elif not java.is_file():
        problems.append(f"java_home 下缺 bin/{java_name}(深校验)")
    return problems


@dataclass
class EffectiveRuntime:
    """resolver 输出(spec A11): 生效四路径 + 各自来源。

    source 取值(RuntimeSource): profile / runtime-bundle / profile-unverified。
    值形态纪律(与消费方 from_profile 的 expanduser 分工对齐):
    - verified(profile 深校验过 / runtime-bundle 探到): 恒为**绝对 posix 形**
      (`Path(...).as_posix()`), 三路径 + indexer_jar 同款, 可直接吞。
    - profile-unverified: 恒为 **profile 原串透传**(不 expanduser、不重整形),
      下游报错口径不变(旧 paths.indexer_jar 也是拿原串自己解析), 只加过日志提示;
      消费方(如 from_profile)自己对这三路径做 expanduser。"""

    jdtls_path: str
    lombok_path: str
    java_home: str
    indexer_jar: str
    trio_source: RuntimeSource
    indexer_source: RuntimeSource


def resolve_effective_runtime(
    profile: Any, root: Path | None = None,
    platform_config: str | None = None,
) -> EffectiveRuntime:
    """生效运行时唯一解析点(spec A11)。优先级 = profile 深校验有效 > bundle >
    透传报错(A4); jdtls 三路径整组判, indexer_jar 独立判(A5)。root 显式收
    (调用方今天传 cwd), 锚点集中可注入。

    profile: 鸭子类型, 期望有 `jdtls_runtime`(转给 validate_profile_runtime_paths
    的 r)与 `code_index.indexer_jar`(str)两个属性(生产传 pydantic Profile,
    测试传轻量 stub)。"""
    root = (root or Path.cwd()).resolve()
    r = profile.jdtls_runtime
    bundle = discover_runtime_bundle(root, platform_config)

    problems = validate_profile_runtime_paths(r, platform_config)
    if not problems:
        trio = (Path(r.jdtls_path).expanduser().as_posix(),
                Path(r.lombok_path).expanduser().as_posix(),
                Path(r.java_home).expanduser().as_posix(), "profile")
    elif bundle is not None:
        _log_dedup(log.info, f"trio-bundle:{bundle.jdtls_path}:{problems}",
                   "jdtls_runtime 回退包内运行时(profile 未过深校验: %s): %s",
                   "; ".join(problems), bundle.jdtls_path)
        trio = (bundle.jdtls_path, bundle.lombok_path, bundle.java_home,
                "runtime-bundle")
    else:
        _log_dedup(log.warning, f"trio-unverified:{problems}",
                   "jdtls_runtime 未配置有效路径且未探到 runtime bundle(%s); "
                   "可下载完整包(Release 页), 解压即含运行时", "; ".join(problems))
        # profile-unverified: 真原值透传(不 expanduser/不 str(Path(...)) 重整形) ——
        # Windows 上占位路径(如 "/nonexistent/jdtls")一经 Path() 会被重整形成反斜杠,
        # 与"透传原值, 下游报错口径不变"的注释承诺矛盾, 也会让 CI 断言双平台不一致。
        trio = (r.jdtls_path, r.lombok_path, r.java_home, "profile-unverified")

    jar = Path(profile.code_index.indexer_jar).expanduser()
    jar = jar if jar.is_absolute() else (root / jar)
    if jar.is_file():
        idx = (jar.as_posix(), "profile")
    elif bundle is not None:
        _log_dedup(log.info, f"indexer-bundle:{jar}:{bundle.indexer_jar}",
                   "indexer_jar 回退包内 java-indexer.jar(配置值不存在: %s -> 启用: %s)",
                   jar, bundle.indexer_jar)
        idx = (bundle.indexer_jar, "runtime-bundle")
    else:
        # indexer unverified 与 trio 同款真透传: 恒为 profile 原串(不做 root 拼接/
        # 不重整形), 消费方自行解析 —— 与旧行为等价(旧 paths.indexer_jar 也是拿
        # profile 原串自己相对 cwd 解析, 从未在这里做过 root 拼接后再报错)。
        idx = (profile.code_index.indexer_jar, "profile-unverified")

    return EffectiveRuntime(jdtls_path=trio[0], lombok_path=trio[1],
                            java_home=trio[2], indexer_jar=idx[0],
                            trio_source=trio[3], indexer_source=idx[1])
