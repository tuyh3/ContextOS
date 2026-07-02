"""corpus 物化: 源文档 -> 可 grep 的 sidecar 文本(含截图 OCR)。"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from contextos.corpus import docx_extract
from contextos.corpus.connectors import iter_source
from contextos.corpus.leakage import LeakageGate
from contextos.corpus.ocr.base import OcrBackend
from contextos.corpus.record_store import RecordStore

_log = logging.getLogger(__name__)


def content_hash(data: bytes) -> str:
    """只 hash 源字节内容(spec §4.3: 不掺路径/mtime/commit, 防虚假全量重 OCR)。"""
    return hashlib.sha256(data).hexdigest()


def build_sidecar_text(raw_bytes: bytes, fmt: str, ocr: OcrBackend) -> str:
    fmt = fmt.lower()
    if fmt == "md":
        return raw_bytes.decode("utf-8", errors="ignore")
    if fmt == "png":
        return f"[image 1 OCR]\n{ocr.ocr(raw_bytes)}\n"
    if fmt == "docx":
        parts: list[str] = []
        text = docx_extract.extract_text(raw_bytes)
        if text.strip():
            parts.append(text)
        for i, (name, img) in enumerate(docx_extract.extract_images(raw_bytes), 1):
            parts.append(f"[image {i} OCR ({name})]\n{ocr.ocr(img)}")
        return "\n\n".join(parts) + "\n"
    raise ValueError(f"unsupported fmt for materialization: {fmt!r}")


def _sidecar_rel(rel_path: str, fmt: str) -> str:
    # md 原样镜像; 其余(docx/png)在原相对路径后挂 .md(便于 grep 一致看 .md)
    return rel_path if fmt == "md" else f"{rel_path}.md"


def materialize_corpus(
    sources: list[object],
    materialized_dir: Path,
    store: RecordStore,
    ocr: OcrBackend,
    backend_name: str,
    formats: tuple[str, ...] = ("md", "docx", "png"),
) -> dict[str, int]:
    """遍历全部源 -> leakage -> 逐文档(缓存跳过)物化 -> full cleanup。

    返回统计 {materialized, skipped, failed, deleted}。单文档失败 fail-safe 不挂全局。
    """
    materialized_dir = Path(materialized_dir)
    stats = {"materialized": 0, "skipped": 0, "failed": 0, "deleted": 0}
    seen: set[str] = set()

    for src in sources:
        gate = LeakageGate(exclude_regexes=list(getattr(src, "leakage_exclude_regex", [])))
        for item in iter_source(src):
            if item.fmt not in formats:
                continue
            if not gate.is_allowed(item.rel_path):
                continue
            seen.add(item.rel_path)
            try:
                raw = item.abs_path.read_bytes()
                h = content_hash(raw)
                rec = store.get(item.rel_path)
                # hash 命中还不够: 必须磁盘副本仍在才跳过, 否则 store/磁盘失同步
                # (副本被删/丢失但 record 残留)会永久跳过 -> 语料静默为空。
                if (
                    rec is not None
                    and rec.content_hash == h
                    and (materialized_dir / rec.sidecar_path).exists()
                ):
                    stats["skipped"] += 1
                    continue
                text = build_sidecar_text(raw, item.fmt, ocr)
                sidecar_rel = _sidecar_rel(item.rel_path, item.fmt)
                sidecar_abs = materialized_dir / sidecar_rel
                sidecar_abs.parent.mkdir(parents=True, exist_ok=True)
                sidecar_abs.write_text(text, encoding="utf-8")
                store.upsert(item.rel_path, h, sidecar_rel, backend_name)
                stats["materialized"] += 1
            except Exception as exc:  # fail-safe: 单文档失败不挂全局
                _log.warning("materialize failed for %s: %s", item.rel_path, exc)
                stats["failed"] += 1

    # full cleanup: record 里本轮没出现的 doc_id -> 删 sidecar + record
    for doc_id in store.all_doc_ids() - seen:
        rec = store.get(doc_id)
        if rec is not None:
            (materialized_dir / rec.sidecar_path).unlink(missing_ok=True)
        store.delete(doc_id)
        stats["deleted"] += 1

    return stats
