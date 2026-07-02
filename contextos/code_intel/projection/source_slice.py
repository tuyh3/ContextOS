"""源码切片原语(spec §7): 投影定位 -> 切行 -> 脱敏 -> 元数据。四护栏全在此 chokepoint。

只收 FQN 不收路径(红线 #9 host 不可信): 路径由投影表内部解析, resolve 后必须仍在
source root 前缀下(防符号链接/相对段穿越)。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection.method_resolve import resolve_bare_method_fqn
from contextos.config_dim.sensitive import redact_secrets_in_text, sanitize_text


class SymbolNotFound(LookupError):
    pass


def _locate(engine: Engine, fqn: str) -> tuple[str, int, int, str]:
    """class -> method -> field -> 裸方法 fallback 顺序查 (source_file, start, end, resolved_fqn)。
    field FQN 形如 Class.FIELD: 拆最后一段查 (class_fqn, field_name)。
    fallback 必须最后(既有路径全 miss 才补签名, 零行为变化; 歧义抛 AmbiguousMethodFqn)。"""
    resolved = fqn
    with engine.connect() as conn:
        row = conn.execute(select(S.code_classes.c.source_file, S.code_classes.c.start_line,
                                  S.code_classes.c.end_line)
                           .where(S.code_classes.c.class_fqn == fqn)).first()
        if row is None:
            row = conn.execute(select(S.code_methods.c.source_file, S.code_methods.c.start_line,
                                      S.code_methods.c.end_line)
                               .where(S.code_methods.c.method_fqn == fqn)).first()
        if row is None:
            cls, _, fname = fqn.rpartition(".")
            if cls and fname:
                row = conn.execute(select(S.code_fields.c.source_file, S.code_fields.c.start_line,
                                          S.code_fields.c.end_line)
                                   .where((S.code_fields.c.class_fqn == cls)
                                          & (S.code_fields.c.field_name == fname))).first()
        if row is None:
            hit = resolve_bare_method_fqn(conn, fqn)
            if hit is not None and hit != fqn:           # bare -> qualified, re-query exact
                row = conn.execute(select(S.code_methods.c.source_file,
                                          S.code_methods.c.start_line,
                                          S.code_methods.c.end_line)
                                   .where(S.code_methods.c.method_fqn == hit)).first()
                if row is not None:
                    resolved = hit
        if row is None:
            raise SymbolNotFound(f"symbol not in projection: {fqn}")
        sf, start, end = str(row[0] or ""), int(row[1] or 0), int(row[2] or 0)
    if not sf:
        # 报 resolved(裸名 fallback 命中时为带签名形态)而非原输入: 指向实际命中的行身份
        raise SymbolNotFound(f"symbol has no source_file anchor: {resolved}")
    return sf, start, end, resolved


def get_symbol_source(engine: Engine, *, repo_root: Path, source_roots: list[Path],
                      fqn: str, max_lines: int,
                      sensitive_patterns: list[str]) -> dict[str, Any]:
    sf, start, end, resolved_fqn = _locate(engine, fqn)
    path = (repo_root / sf).resolve()                      # 护栏 1: resolve 后前缀校验
    roots = [r.resolve() for r in source_roots]
    if not any(path == r or path.is_relative_to(r) for r in roots):
        raise SymbolNotFound(f"resolved path outside source roots: {sf}")
    if not path.is_file():
        raise SymbolNotFound(f"file gone: {sf}")

    data = path.read_bytes()
    with engine.connect() as conn:
        indexed_sha = conn.execute(select(S.code_files.c.sha1).where(
            S.code_files.c.file_path == sf)).scalar() or ""
    stale = hashlib.sha1(data).hexdigest() != indexed_sha  # 护栏 2: stale 标记

    lines = data.decode("utf-8", errors="replace").splitlines()
    lo = max(start, 0)
    hi = min(end + 1 if end else len(lines), len(lines))
    sliced = lines[lo:hi]
    truncated = len(sliced) > max_lines                    # 护栏 3: cap
    if truncated:
        sliced = sliced[:max_lines] + [f"... [truncated at {max_lines} lines]"]
    text = "\n".join(sliced)

    redacted_text = redact_secrets_in_text(text)           # 护栏 4: 脱敏 chokepoint
    redacted_text = sanitize_text(redacted_text, sensitive_patterns)
    return {"fqn": fqn, "resolved_fqn": resolved_fqn, "file": sf,
            "line_start": lo, "line_end": hi - 1,
            "source": redacted_text, "stale": stale, "truncated": truncated,
            "redacted": redacted_text != text}
