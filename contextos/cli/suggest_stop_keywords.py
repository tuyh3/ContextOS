"""contextos suggest-stop-keywords: 扫项目源码, 生成停用词草稿供人工核对(spec 附录 D7)。

薄适配: 配 profile -> resolve_source_roots -> write_draft -> 打印草稿路径 + 候选数。
只写 gitignored 草稿(`<data_dir>/stop-keywords.draft.txt`), 绝不碰 default.json / profile /
已激活客户文件(与 write_draft 本身的不变量一致, 这里不重复编排逻辑)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from contextos.code_intel.projection.paths import resolve_source_roots
from contextos.profile.loader import load_profile
from contextos.recall.stop_keywords_gen import write_draft


def suggest_stop_keywords(
    min_files: Annotated[int, typer.Option(help="候选下限: 至少出现在这么多文件")] = 20,
    min_df_ratio: Annotated[float, typer.Option(help="候选下限: df/总文件数比例")] = 0.2,
    profile: Annotated[str | None, typer.Option("--profile", help="profile.toml 路径")] = None,
) -> None:
    """扫源码算 document-frequency, 把过宽词写成草稿(gitignored)。核对后由 profile
    stop_keywords_path 指向激活。绝不改 default.json / profile / 已激活客户文件。"""
    profile_obj = load_profile(Path(profile) if profile else None)
    roots = resolve_source_roots(profile_obj)
    data_dir = Path(profile_obj.storage.data_dir).expanduser()
    count, draft = write_draft(
        roots, exclude_dirs=profile_obj.code.exclude_dirs, data_dir=data_dir,
        min_files=min_files, min_df_ratio=min_df_ratio)
    typer.echo(f"停用词草稿已生成: {draft}({count} 个候选)")
    typer.echo("核对后(删误判行), 把 profile.input.scope.stop_keywords_path 指向它激活。")


def register(app: typer.Typer) -> None:
    """把 suggest-stop-keywords 命令注册进共享 app(main.py 调; 与 init.register 同一模式)。"""
    app.command("suggest-stop-keywords")(suggest_stop_keywords)
