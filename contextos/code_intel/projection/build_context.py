"""profile -> build_context.json(jar 的输入)。

spec §3.1 条件 1(同源强制): source roots / classpath 全部派生自 profile ——
与 JDT LS workspace 配置同一出处, 不允许各配各的。
LP 借鉴 + 去硬编码: 单合并 module(sharedEnv); 仓内 rglob jar(排 /build/ /target/
/test); 全局缓存目录不默认扫, 走 profile.code_index.extra_classpath_dirs 显式表达。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from contextos.code_intel.projection.paths import repo_root, resolve_source_roots

_EXCLUDE_JAR_SEGMENTS = ("/build/", "/target/", "/test")


def _collect_jars(repo: Path, extra_dirs: list[str]) -> list[str]:
    seen_names: set[str] = set()
    out: list[str] = []

    def _add(jar: Path) -> None:
        if jar.name in seen_names:   # 文件名去重(LP 同款, 防不同路径同名 jar 重复)
            return
        seen_names.add(jar.name)
        out.append(str(jar))

    for jar in sorted(repo.rglob("*.jar")):
        # 排除段匹配在仓内相对路径上, 不在绝对路径上 —— 否则仓外祖先目录
        # 含 /test 或 /build/ (如 pytest tmp dir) 会误杀仓内全部 jar。
        rel = "/" + jar.relative_to(repo).as_posix()
        if any(seg in rel for seg in _EXCLUDE_JAR_SEGMENTS):
            continue
        _add(jar)
    for d in extra_dirs:
        dp = Path(d).expanduser().resolve()
        if dp.is_dir():
            for jar in sorted(dp.rglob("*.jar")):
                _add(jar)
    return out


def build_context_dict(profile: Any) -> dict[str, Any]:
    repo = repo_root(profile)
    roots = [str(p) for p in resolve_source_roots(profile)]   # 空 source_roots -> [repo]
    jars = _collect_jars(repo, list(profile.code_index.extra_classpath_dirs))
    return {
        "java_version": profile.code_index.java_version,
        "modules": [{
            "name": "merged",            # 单合并 module: sharedEnv 跨模块 binding(LP 实测)
            "source_roots": roots,
            "classpath_entries": jars,
            "encoding": "UTF-8",
        }],
    }


def write_build_context(profile: Any, dest: Path) -> dict[str, Any]:
    ctx = build_context_dict(profile)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    return ctx


def context_fingerprint(ctx: dict[str, Any]) -> str:
    """build_context 内容 hash(spec §3.1 条件 2 指纹之一)。键序规范化后 sha1。"""
    return hashlib.sha1(
        json.dumps(ctx, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
