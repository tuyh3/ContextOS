"""profile 路径口径 chokepoint(plan review 修订: projects 是 list; source_roots 空=整仓)。

后续所有消费点(build_context / read_symbol / rebuild_entry / watcher / init)只许
import 这里, 不许各自摸 profile 拼路径 —— 口径漂移就是 review 抓的事故源。
全部返回 .resolve() 后的绝对路径(F4: 消 /tmp vs /private/tmp 符号链接失配)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def primary_project(profile: Any) -> Any:
    """v1 单项目口径 = projects[0](与 AppContext.searcher 现状一致)。"""
    if not profile.projects:
        raise ValueError("profile.projects is empty")
    return profile.projects[0]


def repo_root(profile: Any) -> Path:
    return Path(primary_project(profile).path).expanduser().resolve()


def resolve_source_roots(profile: Any) -> list[Path]:
    """schema 语义: source_roots 空列表 = 扫 project.path 整仓(CodeConfig 注释)。"""
    repo = repo_root(profile)
    roots = [Path(r).expanduser() for r in profile.code.source_roots]
    roots = [(r if r.is_absolute() else repo / r).resolve() for r in roots]
    return roots or [repo]


def indexer_jar(profile: Any) -> Path:
    """[code_index].indexer_jar 解析 chokepoint(NIT-1 最终 review): 相对路径挂 cwd
    (仓根约定)。rebuild_entry / init 共用, 不许各自手写拼路径。"""
    jar = Path(profile.code_index.indexer_jar).expanduser()
    return jar if jar.is_absolute() else (Path.cwd() / jar)
