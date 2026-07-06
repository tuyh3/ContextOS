"""profile -> 增量/全量重建胶水 + 跨进程锁(MCP tool / CLI / watcher 共用一个入口)。

R4(T9/T10 对抗 review HIGH): run_incremental 返回 full_rebuild_required 时**必须在
同一持锁块内**接 build_projection 跑全量(spec §5.3 "撞阈值自然走全量"), 出锁再进会
与第二个触发者竞态。sampler=None(无 live JDT 可对照); indexed_commit 取 build 启动前
HEAD(build 期间新 commit 由下轮增量自愈)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from contextos.code_intel.jdtls_provider.config import JdtlsRuntimeConfig
from contextos.code_intel.projection.build import build_projection
from contextos.code_intel.projection.build_context import build_context_dict
from contextos.code_intel.projection.incremental import (
    git_changed_files_real, head_commit_real, run_incremental)
from contextos.code_intel.projection.paths import (
    indexer_jar, repo_root, resolve_source_roots)
from contextos.storage.flock import try_lock


def incremental_rebuild_code(profile: Any, engine: Engine, *, lockfile: Path) -> dict[str, Any]:
    with try_lock(lockfile) as got:
        if not got:
            return {"status": "already_running"}     # spec §8: 不排队阻塞
        repo = repo_root(profile)
        roots = resolve_source_roots(profile)
        data_dir = Path(profile.storage.data_dir).expanduser()
        out_dir = data_dir / "code-index-out"
        jar = indexer_jar(profile)
        ci = profile.code_index
        # resolver 后端(review P1 / spec A11): 不直读 profile.jdtls_runtime.java_home
        # —— 否则占位 profile 下 JDT 半边靠 bundle 回退跑起来, indexer 半边仍拿占位
        # 路径直接崩。经 from_profile 统一走生效运行时解析点。
        rt = JdtlsRuntimeConfig.from_profile(profile)
        res = run_incremental(
            engine=engine, repo_root=repo, source_roots=roots,
            exclude_dirs=list(profile.code.exclude_dirs),
            java_home=rt.java_home, jar=jar, xmx=ci.indexer_xmx,
            build_ctx=build_context_dict(profile), out_dir=out_dir,
            head_commit=head_commit_real(repo), git_changed_files=git_changed_files_real,
            max_files=ci.incremental_max_files)
        if res.get("status") != "full_rebuild_required":
            return res
        head = head_commit_real(repo)            # build 启动前 HEAD
        full = build_projection(
            engine=engine, repo_root=repo, java_home=rt.java_home,
            jar=jar, xmx=ci.indexer_xmx, build_ctx=build_context_dict(profile),
            out_dir=out_dir, indexed_commit=head, sampler=None)
        return {**full, "full_rebuild_executed": True,
                "trigger": res.get("detail", "")}
