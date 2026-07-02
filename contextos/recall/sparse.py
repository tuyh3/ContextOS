"""sparse 召回: ripgrep 在物化语料里多词命中 -> Hit(rel_path, lineno, line)。

MVP 字面金矿(真表名/字段)主要靠这一路。key_entities 是字面标识符(表名/方法名/
FQN), 故用 -F/--fixed-strings 字面匹配(**不**当正则): 否则 '.' / '(' / '[' 等
metachar 会误匹配、漏匹配、甚至单个非法 metachar 让整次搜索 exit 2 + 空输出而静默
全盘丢失其它有效词(review M1)。case-insensitive(自然语言业务文本); 多词之间 OR
(每词一个 -e)。path_prefixes 缩范围(domain hint -> 路径过滤)。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from contextos.util.subproc_text import decode_content, decode_diagnostic, run_rg

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hit:
    rel_path: str
    lineno: int      # 1-based
    line: str


def ripgrep_hits(
    patterns: list[str], root: str | Path, path_prefixes: list[str] | None = None
) -> list[Hit]:
    pats = [p for p in patterns if p.strip()]
    if not pats:
        return []
    root = Path(root)
    # subprocess 下面用 cwd=str(root), 故 search path 相对 root —— 就是 path_prefixes 本身;
    # 绝不能再拼 root/prefix(那样 cwd=root 下又找 root/prefix = root 被算两次)。root 是相对
    # 路径(线上 data_dir='database/materialized')+ 有 path_prefixes(confirmed-cases corpus)时,
    # 旧写法 -> rg ENOENT(exit 2)-> 静默召回为空(live 实测 2026-06-30)。无 prefix 时搜 "."=root。
    search_paths = (list(path_prefixes) if path_prefixes else ["."])
    # -n 行号; -i 大小写不敏感; -F 字面(非正则); --no-heading + --with-filename 统一格式;
    # 每个 entity 一个 -e -> OR 语义且彼此独立(一个词非法不会拖垮其它词)。
    e_flags: list[str] = []
    for pat in pats:
        e_flags += ["-e", pat]
    args = ["-n", "--null", "-i", "-F", "--no-heading", "--with-filename", *e_flags, *search_paths]
    proc = run_rg(args, cwd=str(root))
    # ripgrep exit: 0=有命中; 1=无命中(正常); 其它=真错误(路径不可读等)。
    # 不静默吞错误(review M2): 让 provider 的 fail-safe 捕获 -> miss + 日志可见。
    if proc.returncode not in (0, 1):
        _log.warning("ripgrep error (exit %s): %s",
                     proc.returncode, decode_diagnostic(proc.stderr).strip())
        raise RuntimeError(f"ripgrep_error:exit{proc.returncode}")
    hits: list[Hit] = []
    for record in proc.stdout.split(b"\n"):
        # bytes 协议: path\0lineno:content。守卫跳过无真 NUL 的行(rg 含 NUL 文件出的
        # 'binary file matches' 行无真 NUL, 强行 split(b"\0") 会崩/产假 hit)。
        if not record or b"\0" not in record:
            continue
        path_b, rest = record.split(b"\0", 1)
        if b":" not in rest:
            continue
        lineno_b, content_b = rest.split(b":", 1)     # 首冒号切 lineno; content 内冒号保留
        try:
            lineno = int(lineno_b)
        except ValueError:
            continue
        path_s = os.fsdecode(path_b)                   # path: Unix=surrogateescape / Win=surrogatepass
        line = decode_content(content_b.rstrip(b"\r"))  # content: utf-8 + CRLF \r 去尾
        # 格式: path 可能含相对前缀(./), 归一成相对 root(既有逻辑对 ./ 鲁棒)
        p = Path(path_s)
        try:
            rel = (p if p.is_absolute() else (root / p)).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = path_s.lstrip("./")
        hits.append(Hit(rel_path=rel, lineno=lineno, line=line))
    return hits
