"""Layer 2: 文件发现与分类(移植 LP source_scan.py, 去硬编码走 Profile)。

DAO .sql 识别走 profile.code.dao_sql_patterns(避开 LP 硬编码 /impl/+/src/main/)。
空 patterns -> 全 .sql 当 other_sql。
"""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from contextos.lineage.models import SourceFile
from contextos.profile.schema import CodeConfig, DaoSqlPattern


def scan_sources(repo_root: Path, code: CodeConfig) -> list[SourceFile]:
    """扫描源码仓库, 返回 .sql 和 .java SourceFile 列表。

    source_roots 非空 -> 只扫这些子目录(相对 repo_root); 空 -> 扫全 repo。
    """
    repo_root = Path(repo_root)
    roots = [repo_root / r for r in code.source_roots] if code.source_roots else [repo_root]
    results: list[SourceFile] = []
    for root in roots:
        if not root.is_dir():
            continue
        for fpath in sorted(root.rglob("*")):
            if not fpath.is_file() or fpath.suffix.lower() not in (".sql", ".java"):
                continue
            try:
                rel_path = fpath.relative_to(repo_root).as_posix()
            except ValueError:
                rel_path = fpath.as_posix()
            if _is_excluded(rel_path, code.exclude_dirs):
                continue
            parts = rel_path.split("/")
            module = parts[0] if len(parts) > 1 else ""
            if fpath.suffix.lower() == ".sql":
                language = "sql"
                category = "dao_sql" if _is_dao_sql(rel_path, code.dao_sql_patterns) else "other_sql"
            else:
                language, category = "java", "java"
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            results.append(SourceFile(path=rel_path, language=language,
                                      module=module, category=category, content=content))
    return results


def _is_dao_sql(rel_path: str, patterns: list[DaoSqlPattern]) -> bool:
    """命中任一 DaoSqlPattern 即 dao_sql(规则内 all/any 由 conjunction 决定)。"""
    for pat in patterns:
        if not pat.path_contains:
            continue
        hits = [sub in rel_path for sub in pat.path_contains]
        ok = all(hits) if pat.conjunction == "all" else any(hits)
        if ok:
            return True
    return False


def _is_excluded(rel_path: str, exclude_dirs: list[str]) -> bool:
    """目录排除: glob 模式逐级匹配 / 前缀匹配(移植 LP _is_excluded)。"""
    for pattern in exclude_dirs:
        if "*" in pattern:
            parts = rel_path.split("/")
            pat_parts = pattern.split("/")
            for i in range(len(parts) - len(pat_parts) + 1):
                if all(fnmatch(parts[i + j], pat_parts[j]) for j in range(len(pat_parts))):
                    return True
        else:
            if rel_path.startswith(pattern + "/") or rel_path == pattern:
                return True
    return False
