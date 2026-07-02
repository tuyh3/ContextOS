"""增量更新(契约 §4 + spec §5.2): 两层检测 -> 扩展变更集 -> 阈值回退 -> 子集重投影。

Layer 2 扫描范围 = source roots 文件系统(按 exclude 过滤); code_files 只是对比
基准 —— 拿表当范围会漏未提交新文件(spec 第三轮 review 收口)。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.engine import Engine

from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection import store
from contextos.code_intel.projection.build import (
    Loader, Runner, _default_loader, _default_runner, unresolved_ratio,
)
from contextos.util.subproc_text import decode_content, decode_diagnostic, run_git

GitChangedFiles = Callable[[Path, str], list[str]]   # (repo_root, since_commit) -> 相对路径


@dataclass
class ChangeSet:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def all_present(self) -> list[str]:
        return self.added + self.modified

    def all_files(self) -> list[str]:
        return self.added + self.modified + self.deleted


def git_changed_files_real(repo_root: Path, since_commit: str) -> list[str]:
    """Layer 1: 已提交变更。两点 diff 是端点树对树比较, rebase 后旧 commit 对象
    仍在时增量照常正确; 真触发全量的是 since_commit 对象不存在(prune/shallow)
    或 git 失败 —— 抛错 -> 调用方走全量(NIT7 措辞修正)。

    since_commit 为空 = 非 git 仓(某大型客户代码库实测: 工作目录不是 git checkout)或
    pre-git 基线 -> 返回空清单, 增量退化为 Layer-2-only。Layer 2 的 sha1 全量扫描
    本身完备(added/modified/deleted 全检), Layer 1 只是 commit 感知的补充, 退化零损失。"""
    if not since_commit:
        return []
    proc = run_git(
        ["-C", str(repo_root), "diff", "--name-only", "-z", "--diff-filter=ACDMR",
         f"{since_commit}..HEAD"], timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {decode_diagnostic(proc.stderr)[-500:]}")
    # -z: 原始字节 + NUL 分隔(无 quotepath 八进制 / 无外层引号); os.fsdecode 还原, 过滤 .java
    out: list[str] = []
    for b in proc.stdout.split(b"\0"):
        if not b:
            continue
        name = os.fsdecode(b)
        if name.endswith(".java"):
            out.append(name)
    return out


def head_commit_real(repo_root: Path) -> str:
    proc = run_git(["-C", str(repo_root), "rev-parse", "HEAD"], timeout=30)
    return decode_content(proc.stdout).strip() if proc.returncode == 0 else ""


def _scan_source_roots(repo_root: Path, source_roots: list[Path],
                       exclude_dirs: list[str]) -> dict[str, str]:
    """当前文件集合 {锚路径: sha1}。

    锚路径口径(merge-review HIGH 修订, 与 jsonl_load._rel 同款): 仓内文件 = 相对
    repo_root; **仓外 source root**(profile 允许绝对路径指仓外)的文件 = 绝对路径
    —— 旧实现对仓外文件强行 relative_to(repo_root) 直接 ValueError 崩增量。
    exclude 匹配用相对路径段(同 T6 修法), 不用绝对路径子串 —— 仓外祖先目录
    恰好叫 exclude 名时子串匹配会误杀整仓; 仓内按 repo 相对段(保持既有语义),
    仓外按 root 相对段。"""
    out: dict[str, str] = {}
    for root in source_roots:
        if not root.is_dir():
            continue
        in_repo = root == repo_root or root.is_relative_to(repo_root)
        for p in root.rglob("*.java"):
            rel_parts = (p.relative_to(repo_root).parts if in_repo
                         else p.relative_to(root).parts)
            if any(d in rel_parts for d in exclude_dirs):
                continue
            key = p.relative_to(repo_root).as_posix() if in_repo else p.as_posix()
            out[key] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


def _in_scope(rel: str, repo_root: Path, source_roots: list[Path],
              exclude_dirs: list[str]) -> bool:
    """F4: git 层输出与 _scan_source_roots 同口径过滤 —— source_roots 之外 /
    exclude 目录下的文件不进任何桶(否则全落 deleted 污染计数 + 虚增阈值)。
    source_roots 由调用方传 resolved; repo_root/rel 直接拼不 resolve(rel 来自
    git 不含 symlink 段)。边界判定用 is_relative_to(Windows 阶段2 修订):
    原 str(p).startswith(str(root) + "/") 硬编码 "/" 分隔符, Windows 上
    str(p) 是反斜杠, 这条判定恒假, 会让 Layer-1 git 变更全部判"越界"丢弃。"""
    p = repo_root / rel
    if not any(p == root or p.is_relative_to(root) for root in source_roots):
        return False
    return not any(d in Path(rel).parts for d in exclude_dirs)


def detect_changes(engine: Engine, *, repo_root: Path, source_roots: list[Path],
                   exclude_dirs: list[str],
                   git_changed_files: GitChangedFiles) -> ChangeSet:
    current = _scan_source_roots(repo_root, source_roots, exclude_dirs)
    with engine.connect() as conn:
        baseline = {r[0]: r[1] for r in conn.execute(
            select(S.code_files.c.file_path, S.code_files.c.sha1))}
    cs = ChangeSet()
    for rel in sorted(current.keys() - baseline.keys()):
        cs.added.append(rel)
    for rel in sorted(current.keys() & baseline.keys()):
        if current[rel] != baseline[rel]:
            cs.modified.append(rel)
    for rel in sorted(baseline.keys() - current.keys()):
        cs.deleted.append(rel)
    # Layer 1 合并(git 报的但 sha1 没差的不重复; 报了且文件存在 -> modified, 不存在 -> deleted)
    last = store.get_meta(engine, "last_indexed_commit") or ""
    seen = set(cs.all_files())
    for rel in git_changed_files(repo_root, last):
        if rel in seen or not _in_scope(rel, repo_root, source_roots, exclude_dirs):
            continue
        (cs.modified if rel in current else cs.deleted).append(rel)
        seen.add(rel)
    return cs


def expand_changed(engine: Engine, changed_files: list[str]) -> list[str]:
    """扩展变更集(契约 §4.1): changed 文件里定义的 FQN 被谁引用 -> 引用方文件拉进重解析。

    入参必须含 **deleted** 文件(review HIGH): 删除/rename 后引用方若不重解析,
    旧 resolved reference 永久残留为脏数据。查询基于删除前的投影行(此刻还没删),
    所以 deleted 文件定义的 FQN 仍能反查到引用方。"""
    if not changed_files:
        return []
    with engine.connect() as conn:
        fqns = [r[0] for r in conn.execute(
            select(S.code_classes.c.class_fqn).where(
                S.code_classes.c.source_file.in_(changed_files)))]
        if not fqns:
            return []
        referrers = [r[0] for r in conn.execute(
            select(S.code_references.c.source_file).distinct().where(
                S.code_references.c.target_fqn.in_(fqns)))]
    return sorted(set(referrers) - set(changed_files))


def fingerprints_changed(engine: Engine, *, jar: Path, build_ctx: dict[str, Any],
                         java_home: str) -> str | None:
    """spec §3.1 条件 2: 运行时指纹与上次 build 不一致 -> 返回不一致项描述(调用方强制全量);
    一致或无基准(首建由 plan_incremental 兜)返回 None。

    单点收口在增量入口(rebuild_entry -> run_incremental), watcher / MCP / CLI 全部受益;
    换 jar / 改 build_ctx / 换 JDK 后增量照跑会产"半新半旧"投影 + meta 撒谎(最终 review HIGH-1)。
    """
    import platform

    from contextos.code_intel.projection.build_context import context_fingerprint
    from contextos.code_intel.projection.indexer_runner import jar_fingerprint
    checks = (
        ("jar_hash", jar_fingerprint(jar) if jar.exists() else ""),
        ("build_context_hash", context_fingerprint(build_ctx)),
        ("jdk_fingerprint", f"{java_home}|{platform.machine()}"),
    )
    diffs: list[str] = []
    for key, current in checks:
        stored = store.get_meta(engine, key)
        if stored is not None and stored != "" and stored != current:
            diffs.append(key)
    return ",".join(diffs) if diffs else None


def plan_incremental(engine: Engine, *, changed: list[str], max_files: int) -> str:
    """'incremental' | 'full' | 'noop'(契约 §4.3 本层可判的两条: 超阈值 / 无基准。
    merge-base 断裂由 git_changed_files 抛错走 full, schema 升级由
    ensure_projection_schema 自理)。

    基准信号 = projection_build_id(build 发生过), **不是** last_indexed_commit ——
    非 git 仓(某大型客户代码库工作目录)commit 恒空, 拿它当基准会把每次增量误判全量
    (~分钟级/次, watcher 形同虚设); sha1 基准在 code_files 表里, 与 git 无关。"""
    if not changed:
        return "noop"
    if not store.get_meta(engine, "projection_build_id"):
        return "full"
    if len(changed) > max_files:
        return "full"
    return "incremental"


def run_incremental(*, engine: Engine, repo_root: Path, source_roots: list[Path],
                    exclude_dirs: list[str], java_home: str, jar: Path, xmx: str,
                    build_ctx: dict[str, Any], out_dir: Path, head_commit: str,
                    git_changed_files: GitChangedFiles,
                    runner: Runner = _default_runner, loader: Loader = _default_loader,
                    max_files: int = 500, unresolved_max: float = 0.15) -> dict[str, Any]:
    S.ensure_projection_schema(engine)
    # spec §3.1 条件 2(最终 review HIGH-1): 指纹比对在一切变更检测之前 ——
    # 指纹不一致时增量结果必为半新半旧, 直接强制全量(rebuild_entry 同锁内自动接)。
    diff = fingerprints_changed(engine, jar=jar, build_ctx=build_ctx, java_home=java_home)
    if diff:
        return {"status": "full_rebuild_required", "detail": f"fingerprint changed: {diff}"}
    try:
        cs = detect_changes(engine, repo_root=repo_root, source_roots=source_roots,
                            exclude_dirs=exclude_dirs, git_changed_files=git_changed_files)
    except (RuntimeError, FileNotFoundError) as exc:
        # since_commit 对象不存在(prune/shallow)/ git 失败 / git binary 缺失(NIT8)
        # -> 全量回退信号
        return {"status": "full_rebuild_required", "detail": str(exc)}

    # 扩展来源 = 全变更集含 deleted(review HIGH); 但只把仍存在的文件喂 jar
    expanded = expand_changed(engine, cs.all_files())
    reparse = sorted((set(cs.all_present()) | set(expanded)) - set(cs.deleted))
    plan = plan_incremental(engine, changed=reparse + cs.deleted, max_files=max_files)
    if plan == "noop":
        return {"status": "noop", "detail": "no changes"}
    if plan == "full":
        return {"status": "full_rebuild_required",
                "detail": f"{len(reparse) + len(cs.deleted)} files (threshold {max_files}) or no baseline"}

    rows: dict[str, list[dict[str, Any]]] = {}
    if reparse:
        ctx_file = out_dir / "build_context.json"
        out_dir.mkdir(parents=True, exist_ok=True)
        ctx_file.write_text(json.dumps(build_ctx, ensure_ascii=False), encoding="utf-8")
        files_list = out_dir / "files.txt"
        files_list.write_text(
            "\n".join(str(repo_root / f) for f in reparse) + "\n", encoding="utf-8")
        try:
            runner(java_home=java_home, jar=jar, xmx=xmx, ctx_file=ctx_file,
                   out_dir=out_dir, files_list=files_list)
            rows = dict(loader(out_dir, repo_root=repo_root))
        except Exception as exc:   # 保旧
            return {"status": "degraded", "detail": f"{type(exc).__name__}: {exc}"}
        # 防御(第三轮 review MEDIUM): 子集运行的产出只该锚在 reparse 文件上;
        # 越界锚丢弃(stale/越权), 空锚行保留(runner 已清目录, 必为本次产出)
        keep = set(reparse)

        def _anchor(r: dict[str, Any]) -> str:
            return str(r.get("source_file") or r.get("file_path") or "")

        rows = {name: [r for r in rs if not _anchor(r) or _anchor(r) in keep]
                for name, rs in rows.items()}

    # F5: 子集质量检查(事务前, 对过滤后的 rows)。超阈仍换新(数据可用),
    # 但 build_status 降 degraded + detail 警示 —— 坏环境(classpath 损坏)下
    # 100% unresolved 不能让 freshness 继续报 ok。未超阈不碰 build_status(全仓语义)。
    sub_unres = unresolved_ratio(rows)
    detail = ""
    if sub_unres > unresolved_max:
        detail = f"subset unresolved ratio {sub_unres:.2%} > {unresolved_max:.0%}"

    touched = sorted(set(reparse) | set(cs.deleted))
    with engine.begin() as conn:   # 事务: 删旧插新 + meta(spec §5.2)
        store.delete_rows_for_files_conn(conn, touched)
        store.insert_rows_conn(conn, rows)
        store.set_meta_conn(conn, "last_indexed_commit", head_commit)
        store.set_meta_conn(conn, "projection_build_id", uuid.uuid4().hex[:12])
        if detail:
            store.set_meta_conn(conn, "build_status", "degraded")
    return {"status": "ok", "detail": detail,
            "added": len(cs.added), "modified": len(cs.modified),
            "deleted": len(cs.deleted), "reparsed": len(reparse)}
