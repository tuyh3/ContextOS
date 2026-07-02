"""LocalFSBackend covers Z 决策:hash 去重 + 自包含 + sanitized basename."""
from __future__ import annotations

from pathlib import Path

import pytest

from contextos.storage.backend import LocalFSBackend


def test_put_uses_hash_prefixed_sanitized_basename(tmp_path: Path) -> None:
    backend = LocalFSBackend(root=tmp_path)
    src = tmp_path / "raw" / "需求 A (final).docx"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"sample bytes")

    stored = backend.put(src, namespace="source-docs")

    assert stored.parent.name == "source-docs"
    parts = stored.name.split("-", 1)
    assert len(parts[0]) == 8 and all(c in "0123456789abcdef" for c in parts[0])
    assert parts[1].endswith(".docx")
    assert " " not in parts[1] and "(" not in parts[1]


def test_put_is_idempotent_on_same_content(tmp_path: Path) -> None:
    backend = LocalFSBackend(root=tmp_path)
    src = tmp_path / "a.docx"
    src.write_bytes(b"same content")
    first = backend.put(src, namespace="source-docs")
    second = backend.put(src, namespace="source-docs")
    assert first == second
    files = list((tmp_path / "source-docs").iterdir())
    assert len(files) == 1


def test_exists_and_get_and_list(tmp_path: Path) -> None:
    backend = LocalFSBackend(root=tmp_path)
    src = tmp_path / "b.docx"
    src.write_bytes(b"content")
    stored = backend.put(src, namespace="source-docs")

    assert backend.exists(stored)
    assert backend.get(stored) == b"content"
    listed = list(backend.list("source-docs"))
    assert stored in listed


def test_put_rejects_traversal_namespace(tmp_path: Path) -> None:
    backend = LocalFSBackend(root=tmp_path)
    src = tmp_path / "c.docx"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="invalid namespace"):
        backend.put(src, namespace="../escape")
