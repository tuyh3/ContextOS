"""source 连接器(可插拔): SourceConfig -> 文件清单。

git / dir 在 MVP 都走本地文件遍历(location 是本地路径)。git pull / commit 记录
留后续(本 plan 不做)。加新源类型(wiki/confluence) = 加分支, 不改下游。
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileItem:
    rel_path: str   # 源内相对路径(= doc_id 锚点, 稳定)
    abs_path: Path
    fmt: str        # 小写后缀(md/docx/png ...)


def iter_source(src: object) -> Iterator[FileItem]:
    base = Path(getattr(src, "location")).expanduser()
    patterns = list(getattr(src, "glob"))
    seen: set[str] = set()
    for pattern in patterns:
        for p in base.glob(pattern):
            if not p.is_file():
                continue
            if p.name.startswith("~$"):
                # MS Office 锁定/属主临时文件(开着 Word/Excel 时生成的 ~$xxx.docx):
                # 不是真文档, 物化会 'File is not a zip file' 失败 -> 让 corpus 维一直 degraded。
                continue
            rel = p.relative_to(base).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            yield FileItem(rel_path=rel, abs_path=p, fmt=p.suffix.lstrip(".").lower())
