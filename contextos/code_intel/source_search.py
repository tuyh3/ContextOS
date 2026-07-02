"""search_source: 原始源码文本检索(覆盖/grounding 基座 ①)。

补 MCP 证据层盲区: 方法体内字面量(框架字符串派发 / 内联 startsWith / 配置文件文本)
既不进符号索引(search_code)也不进恢复 SQL(search_sql), 只有原始 grep 找得到。
服务端 owns rg 子进程(host 零 shell), 只搜 resolve_source_roots(profile), 脱敏后返回。

证据等级 = text-hit(弱证据, 低于 JDT/投影符号事实): .java 投影内命中回填 enclosing FQN
(host 可 read_symbol 升级); 非 Java/配置命中本身即 text-hit。照 recall/sparse.py ripgrep_hits
范式 + source_slice.py 路径安全/脱敏 chokepoint。caps 服务端固定(host 不可设, 防滥用成任意 grep)。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contextos.util.subproc_text import decode_content, decode_diagnostic, run_rg

from sqlalchemy import select
from sqlalchemy.engine import Engine

from contextos.code_intel.projection import schema as S
from contextos.config_dim.sensitive import redact_secrets_in_text, sanitize_text

# --- 固定上限(服务端 owns; host 不可设) ---
_DEFAULT_LIMIT = 200                       # 总命中上限
_DEFAULT_MAX_MATCHES_PER_FILE = 20         # 单文件命中上限
_DEFAULT_MAX_FILES_SCANNED = 5000          # 预选(considered)文件上限
_DEFAULT_MAX_BYTES_PER_FILE = 2_000_000    # 单文件大小上限(超出预选剔除)
_MAX_CONTEXT_LINES = 5
_RG_TIMEOUT_S = 30
_RG_BATCH = 500                            # phase2 每批文件数(防 macOS ARG_MAX)
_DEFAULT_EXTENSIONS = (
    ".java", ".xml", ".html", ".page", ".jsp", ".properties",
    ".sql", ".js", ".ts", ".yml", ".yaml", ".toml",
)


class SearchSourceError(RuntimeError):
    """search_source 失败基类(MCP 层转 ToolError)。"""


class RipgrepUnavailable(SearchSourceError):
    """rg 不在 PATH。显式抛(不静默降级), 由 MCP 层转 ToolError + 装 rg 提示。"""


@dataclass
class _Candidate:
    abs_path: Path
    root: Path
    rel_to_root: str            # 相对 source_root(posix)
    repo_relative: str | None   # 相对 repo_root(仅仓内); 仓外根 -> None


@dataclass
class _Preselect:
    candidates: list[_Candidate]
    files_scanned: int          # = len(candidates)(考量数, 非命中数)
    truncated: bool             # 超大剔除 或 files cap 触发 -> 覆盖不完整


def _glob_flags(extensions: list[str], exclude_dirs: list[str]) -> list[str]:
    """扩展名 allowlist(include glob) + exclude_dirs 嵌套排除(!glob, 非只顶层)。"""
    flags: list[str] = []
    for ext in extensions:
        e = ext if ext.startswith(".") else f".{ext}"
        flags += ["-g", f"*{e}"]
    for d in exclude_dirs:
        flags += ["-g", f"!**/{d}/**"]
    return flags


def _list_root_files(root: Path, glob_flags: list[str]) -> list[str]:
    """rg --files 列 root 下候选(相对 root 的 posix 路径)。--no-ignore --hidden:
    文件域=全量(不被 .gitignore 漏), 排除完全交给 exclude_dirs glob(确定性)。
    --null: NUL 分隔字节路径(空格/非 ASCII 不碎); 不传末尾 '.' 参数(已 cwd=root)以免 rg 加 ./ 前缀。
    --path-separator /: rg 默认 Windows 用 '\\' 分隔输出(rg --help 实证), 显式锁 '/' 兑现本函数
    'posix 路径'契约(三平台一致)—— 本函数返回值喂 _preselect 的 (r/rel) + as_posix, 用 '/' 安全。"""
    proc = run_rg(["--files", "--null", "--path-separator", "/",
                   "--no-ignore", "--hidden", *glob_flags],
                  cwd=str(root), timeout=_RG_TIMEOUT_S)
    if proc.returncode not in (0, 1):       # 0=有 1=无, 其它=真错误
        raise SearchSourceError(
            f"rg --files error (exit {proc.returncode}): {decode_diagnostic(proc.stderr).strip()}")
    # path 一律从 abs_path 重算(不信 rg 原串, MEDIUM-2); 这里只把 rg 回吐 bytes 解成相对 root posix
    return [os.fsdecode(b) for b in proc.stdout.split(b"\0") if b]


def _preselect(*, repo_root: Path, source_roots: list[Path], extensions: list[str],
               exclude_dirs: list[str], max_files_scanned: int,
               max_bytes_per_file: int) -> _Preselect:
    """候选稳定排序 (source_root 顺序, 相对路径) + 超大剔除 + max_files cap。
    truncated 仅在**真有候选被 cap 丢弃 / 超大剔除**时置(精确; 不在"恰好填满 cap"误降级, MEDIUM-3)。
    path 一律从 abs_path 重算 as_posix(不信 rg 原串; MEDIUM-2)。"""
    flags = _glob_flags(extensions, exclude_dirs)
    rr = repo_root.resolve()
    selected: list[_Candidate] = []
    truncated = False
    capped = False
    for root in source_roots:                       # 第一维: source_root 顺序
        if capped:
            break
        r = root.resolve()
        if not r.is_dir():
            continue
        in_repo = r == rr or r.is_relative_to(rr)
        for rel in sorted(_list_root_files(r, flags)):   # 第二维: 相对路径稳定排序
            if len(selected) >= max_files_scanned:
                truncated = True                    # 真有候选超 cap -> 覆盖不完整
                capped = True
                break
            abs_path = (r / rel).resolve()
            try:
                if abs_path.stat().st_size > max_bytes_per_file:
                    truncated = True                # 超大剔除 -> 覆盖不完整
                    continue
            except OSError:
                continue
            repo_rel = abs_path.relative_to(rr).as_posix() if in_repo else None
            selected.append(_Candidate(
                abs_path, r, abs_path.relative_to(r).as_posix(), repo_rel))
    return _Preselect(selected, len(selected), truncated)


# 扩展名形态(MEDIUM-1): .?字母数字开头 + [字母数字_+-], 1-12 字符; 拒 glob 元字符(* ? [ ] { } ! /)与空格。
_EXT_RE = re.compile(r"^\.?[A-Za-z0-9][A-Za-z0-9_+-]{0,11}$")


def _normalize_extensions(file_extensions: list[str] | None) -> list[str]:
    """host 传的扩展名 allowlist: 校验格式(拒 glob 元字符, 防 ["*"]/[".*"] 扩成 *.* 全扫)。
    缺省/空 -> 默认文本 allowlist。"""
    if not file_extensions:
        return list(_DEFAULT_EXTENSIONS)
    out: list[str] = []
    for raw in file_extensions:
        e = raw if raw.startswith(".") else f".{raw}"
        if not _EXT_RE.match(e):
            raise SearchSourceError(f"invalid file_extension: {raw!r}")
        out.append(e.lower())
    return out


def _build_search_cmd(query: str, mode: str, case_sensitive: bool,
                      files: list[str]) -> list[str]:
    """run_rg 的 args(不含 'rg', helper 前置 rg + --no-config --color=never)。
    --null: 内容流字节协议 path\\0lineno:content(消盘符冒号歧义)。
    **不加 --path-separator**(与 _list_root_files 不同): 内容流传**显式绝对文件 args**, rg 原样
    回吐该 path; 调用方 by_abs 以 str(abs_path)(Windows 原生 '\\')精确匹配 —— 强制 '/' 会让
    Windows 上 rg 回吐('/')与 by_abs 键('\\')对不上, 命中全丢。故内容流保持平台原生分隔符。"""
    args = ["-n", "--null", "--no-heading", "--with-filename"]
    if not case_sensitive:
        args.append("-i")
    if mode == "literal":
        args.append("-F")                    # 字面; regex 模式走 rg 线性引擎(无 ReDoS)
    args += ["-e", query, *files]
    return args


def _search_batch(query: str, mode: str, case_sensitive: bool,
                  files: list[str]) -> list[tuple[str, int, str]]:
    """一批文件上跑 rg, 返回 (abs_path, lineno, line)。"""
    args = _build_search_cmd(query, mode, case_sensitive, files)
    try:
        proc = run_rg(args, timeout=_RG_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise SearchSourceError(
            f"rg timeout ({_RG_TIMEOUT_S}s); 收窄 query 或扩展名") from exc
    if proc.returncode not in (0, 1):
        raise SearchSourceError(
            f"rg error (exit {proc.returncode}): {decode_diagnostic(proc.stderr).strip()}")
    out: list[tuple[str, int, str]] = []
    for record in proc.stdout.split(b"\n"):
        # bytes 协议 path\0lineno:content; 守卫跳过无真 NUL 行(含 NUL 文件的 binary file matches)
        if not record or b"\0" not in record:
            continue
        path_b, rest = record.split(b"\0", 1)
        if b":" not in rest:
            continue
        lineno_b, content_b = rest.split(b":", 1)
        try:
            lineno = int(lineno_b)
        except ValueError:
            continue
        out.append((os.fsdecode(path_b), lineno, decode_content(content_b.rstrip(b"\r"))))
    return out


def _snippet(abs_path: Path, match_line: str, lineno: int, context_lines: int) -> str:
    if context_lines <= 0:
        return match_line
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return match_line
    lines = text.splitlines()
    lo = max(0, lineno - 1 - context_lines)
    hi = min(len(lines), lineno + context_lines)
    return "\n".join(lines[lo:hi])


def _backfill_fqn(engine: Engine | None, cand: _Candidate,
                  line: int) -> tuple[str | None, str | None]:
    """.java 投影内命中: code_methods 反查 (source_file == sf AND start<=line<=end)。
    取最内层(start_line 最大)。无方法命中退 code_classes 取 enclosing class。
    sf 口径与投影锚一致(Windows 阶段2 整族): 仓内文件 = repo_relative(relative_to(repo_root).as_posix());
    仓外 source root 文件 = abs_path.as_posix()(与 jsonl_load._rel / incremental._scan_source_roots
    仓外分支同口径, 都 as_posix)-> 仓外 .java 命中也能桥到 enclosing FQN(I-1), 且 Windows 上
    正斜杠锚与投影 DB 一致不失配。"""
    sf = cand.repo_relative or cand.abs_path.as_posix()
    if engine is None or not sf:
        return (None, None)
    with engine.connect() as conn:
        mrow = conn.execute(
            select(S.code_methods.c.method_fqn, S.code_methods.c.class_fqn)
            .where((S.code_methods.c.source_file == sf)
                   & (S.code_methods.c.start_line <= line)
                   & (S.code_methods.c.end_line >= line))
            .order_by(S.code_methods.c.start_line.desc())
        ).first()
        if mrow is not None:
            return (str(mrow[1] or "") or None, str(mrow[0] or "") or None)
        crow = conn.execute(
            select(S.code_classes.c.class_fqn)
            .where((S.code_classes.c.source_file == sf)
                   & (S.code_classes.c.start_line <= line)
                   & (S.code_classes.c.end_line >= line))
            .order_by(S.code_classes.c.start_line.desc())
        ).first()
        if crow is not None:
            return (str(crow[0] or "") or None, None)
    return (None, None)


def search_source(
    *,
    repo_root: Path,
    source_roots: list[Path],
    query: str,
    sensitive_patterns: list[str],
    mode: str = "literal",
    case_sensitive: bool = False,
    context_lines: int = 0,
    file_extensions: list[str] | None = None,
    exclude_dirs: list[str] | None = None,
    engine: Engine | None = None,
    limit: int = _DEFAULT_LIMIT,
    max_matches_per_file: int = _DEFAULT_MAX_MATCHES_PER_FILE,
    max_files_scanned: int = _DEFAULT_MAX_FILES_SCANNED,
    max_bytes_per_file: int = _DEFAULT_MAX_BYTES_PER_FILE,
) -> dict[str, Any]:
    if not query or not query.strip():
        raise SearchSourceError("empty query")
    if shutil.which("rg") is None:
        raise RipgrepUnavailable("ripgrep (rg) not on PATH; install rg")
    if mode not in ("literal", "regex"):
        raise SearchSourceError(f"invalid mode: {mode!r}")
    cl = max(0, min(context_lines, _MAX_CONTEXT_LINES))
    exts = _normalize_extensions(file_extensions)   # MEDIUM-1: 拒 glob 元字符
    excl = list(exclude_dirs) if exclude_dirs is not None else []
    rr = repo_root.resolve()
    roots = [r.resolve() for r in source_roots]

    pre = _preselect(repo_root=rr, source_roots=roots, extensions=exts,
                     exclude_dirs=excl, max_files_scanned=max_files_scanned,
                     max_bytes_per_file=max_bytes_per_file)
    by_abs = {str(c.abs_path): c for c in pre.candidates}
    files = [str(c.abs_path) for c in pre.candidates]

    results: list[dict[str, Any]] = []
    per_file_counts: dict[str, int] = {}
    truncated = pre.truncated
    per_file_truncated = False
    total = 0
    for bi in range(0, len(files), _RG_BATCH):
        stop = False
        for path_s, lineno, line in _search_batch(
                query, mode, case_sensitive, files[bi:bi + _RG_BATCH]):
            cand = by_abs.get(path_s)
            if cand is None:
                continue
            # 路径安全冗余守: 命中文件必在某 source_root 下(预选已保证, 防 symlink 边角)
            if not any(cand.abs_path == r or cand.abs_path.is_relative_to(r)
                       for r in roots):
                continue
            cnt = per_file_counts.get(path_s, 0)
            if cnt >= max_matches_per_file:
                per_file_truncated = True
                continue
            if total >= limit:
                truncated = True        # 看到第 limit+1 个命中 -> 丢弃, 覆盖不完整
                stop = True
                break
            snippet = _snippet(cand.abs_path, line, lineno, cl)
            snippet = sanitize_text(redact_secrets_in_text(snippet), sensitive_patterns)
            ext = cand.abs_path.suffix.lower()
            enc_class, enc_method = (
                _backfill_fqn(engine, cand, lineno)
                if (engine is not None and ext == ".java")
                else (None, None))
            results.append({
                "root": str(cand.root),
                "path": cand.rel_to_root,
                "repo_relative": cand.repo_relative,
                "line": lineno,
                "snippet": snippet,
                "ext": ext,
                "evidence_tier": "text-hit",
                "enclosing_class_fqn": enc_class,
                "enclosing_method_fqn": enc_method,
            })
            per_file_counts[path_s] = cnt + 1
            total += 1
        if stop:
            break
        # 已满 limit 且仍有未扫描 batch -> 覆盖不完整(恰好填满且无剩余 batch 不误判, MEDIUM-1)
        if total >= limit and bi + _RG_BATCH < len(files):
            truncated = True
            break
    return {
        "searched_roots": [str(r) for r in roots],
        "results": results,
        "total_matches": total,
        "files_scanned": pre.files_scanned,
        "truncated": truncated,
        "per_file_truncated": per_file_truncated,
    }
