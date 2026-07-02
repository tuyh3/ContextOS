"""Per-project workspace root dir for JDT LS / solidlsp.

Each project gets a unique workspace root (md5 hash of project path so distinct
projects can't collide). solidlsp + JDT LS create their own subdirectories
underneath (`solidlsp_static/`, `project_data/`, `.metadata/`, OSGi config, etc.)
based on the SolidLSPSettings keys the adapter passes — we don't pre-create
those here, because the subdir names are owned by solidlsp's API, not ours.
"""
import hashlib
from pathlib import Path


def workspace_dir_for(base_dir: Path, project_path: str) -> Path:
    """Returns (and creates if missing) the per-project workspace root.

    Layout:
        <base_dir>/
            <md5(project_path)>/        <- this directory; solidlsp + JDT LS
                                            create their own subdirs underneath

    Only the root dir is created here. Do not assume any specific subdir layout
    on this path — the adapter (via SolidLSPSettings) decides what subdirs are
    used and solidlsp creates them on first use.
    """
    h = hashlib.md5(project_path.encode("utf-8")).hexdigest()
    # resolve() -> 绝对路径。必须绝对: 这个路径会被拼进 JDT 的 `-data`/`-configuration`
    # 命令行交给 java 子进程, 而子进程的 cwd = 被分析项目根(如某电信客户项目), 不是 contextos
    # 仓根。相对 base_dir(profile data_dir 改相对 'database' 后)在子进程里会解析到
    # 错误的根 -> Equinox 找不到 python 端填好的 config -> 启动即猝死(2026-06-07 真跑坐实)。
    # 相对路径锚到 cwd(= 仓根, 所有入口从仓根运行的契约), 与 sqlite data_dir 相对 cwd 行为一致。
    ws = (base_dir / h).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    return ws
