"""subprocess 驱动 vendored java-indexer jar(全量 / --files 子集)。

快照即弃(spec §3.1 条件 4): 进程跑完即退, binding 状态不跨 build 存活。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

# jar 的全部已知输出文件: SSOT 在 jsonl_load.JSONL_NAMES(loader 读哪些, runner 就清哪些;
# 共享常量防漂移——新增输出文件只改 loader 清单, 清残留自动跟上)。
# 每次运行前清掉, 防 out_dir 复用时 loader 把上次残留的 JSONL 当本次结果灌库。
from contextos.code_intel.projection.jsonl_load import JSONL_NAMES as _RUN_OUTPUTS
from contextos.util.subproc_text import decode_diagnostic


class IndexerError(RuntimeError):
    pass


def build_command(*, java_home: str, jar: Path, xmx: str, ctx_file: Path,
                  out_dir: Path, files_list: Path | None) -> list[str]:
    # profile 的 java_home 可能写 "~/..."(JDT 侧 JdtlsRuntimeConfig.from_profile 会
    # expanduser, 这里是 jar 侧同款 chokepoint): subprocess 不展开 ~, 某大型客户代码库冒烟实测坐实
    if not java_home:
        java = "java"                    # PATH 上的 java, 三平台同语义
    elif sys.platform == "win32":
        # Windows 可执行名带 .exe(照搬 vendored eclipse_jdtls 平台判法);
        # CreateProcess 或许容忍缺 .exe, 但修法便宜不赌(spec 附录C)
        java = str(Path(java_home).expanduser() / "bin" / "java.exe")
    else:
        java = f"{Path(java_home).expanduser()}/bin/java"
    cmd = [java, f"-Xmx{xmx}", "-jar", str(jar), str(ctx_file), str(out_dir)]
    if files_list is not None:
        cmd += ["--files", str(files_list)]
    return cmd


def jar_fingerprint(jar: Path) -> str:
    """spec §3.1 条件 2 指纹之一。15MB 读 hash 几十 ms, build 期一次, 可接受。"""
    return hashlib.sha1(jar.read_bytes()).hexdigest()


def run_indexer(*, java_home: str, jar: Path, xmx: str, ctx_file: Path, out_dir: Path,
                files_list: Path | None = None, timeout_seconds: int = 1800,
                _java_override: str = "") -> None:
    if not jar.exists():
        raise IndexerError(
            f"indexer jar not found: {jar}; build it per vendor/java-indexer/README.md "
            f"(cd vendor/java-indexer && mvn -q package -DskipTests)")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in _RUN_OUTPUTS:          # 清残留(见 _RUN_OUTPUTS 注释)
        stale = out_dir / name
        if stale.exists():
            stale.unlink()
    cmd = build_command(java_home=java_home, jar=jar, xmx=xmx, ctx_file=ctx_file,
                        out_dir=out_dir, files_list=files_list)
    if _java_override:
        cmd[0] = _java_override
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:  # 契约统一: 本模块失败 = IndexerError
        raise IndexerError(f"java-indexer timeout after {timeout_seconds}s") from exc
    if proc.returncode != 0:
        tail = decode_diagnostic((proc.stderr or b"")[-2000:])   # bytes 尾 -> utf-8 replace 解码
        raise IndexerError(f"java-indexer exit {proc.returncode}: {tail}")
