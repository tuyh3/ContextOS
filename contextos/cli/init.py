"""`contextos init` 命令(spec §8)。薄适配: 配 logging + 调 run_init + 汇总表 + exit code。
编排逻辑在 contextos.init.orchestrator, 本层不写编排。"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from contextos.init.orchestrator import run_init
from contextos.init.report import InitReport
from contextos.profile.loader import load_profile

_EXIT = {"ready": 0, "degraded": 1, "aborted": 2}


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:                            # 同进程多次调用(测试/复用)不累积 handler 防双重日志
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        root.addHandler(handler)


def _print_summary(report: InitReport) -> None:
    print("\n=== contextos init 汇总 ===")
    for s in report.steps:
        line = f"  [{s.status:8}] {s.dimension:9} {s.counts}"
        if s.detail:
            line += f"  ({s.detail})"
        print(line)
    print(f"verdict: {report.verdict}")
    if report.reasons:
        for r in report.reasons:
            print(f"  - {r}")


def init(
    profile: Annotated[str | None, typer.Option("--profile", help="profile.toml 路径")] = None,
    only: Annotated[str | None, typer.Option("--only", help="只 build 单维度: code|database|config|corpus")] = None,
    skip_oracle: Annotated[bool, typer.Option("--skip-oracle", help="不连 Oracle, 只静态血缘")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="DEBUG 日志")] = False,
) -> None:
    """初始化客户: build 四个证据维度, 跑完直接可查。"""
    _configure_logging(verbose)
    # MED-4: profile 加载失败(文件缺失/TOML 解析错)必须是干净 aborted(exit 2), 不能让
    # FileNotFoundError 等冒泡成 exit 1 + 原始 traceback —— 那会与 degraded(exit 1)撞码,
    # CI / 脚本无法区分'降级'与'profile 崩'。load_profile 在 run_init 的 try 之外, 故单独兜。
    try:
        prof = load_profile(Path(profile) if profile else None)
    except Exception as exc:  # noqa: BLE001
        print("\n=== contextos init 中止 ===")
        print(f"profile 加载失败: {type(exc).__name__}: {exc}")
        raise typer.Exit(_EXIT["aborted"]) from None
    report = run_init(prof, now=datetime.now().isoformat(), only=only, skip_oracle=skip_oracle)
    _print_summary(report)
    raise typer.Exit(_EXIT[report.verdict])


def register(app: typer.Typer) -> None:
    """把 init 命令注册进共享 app(main.py 调; 避免 init.py 反向 import main 循环)。"""
    app.command("init")(init)
