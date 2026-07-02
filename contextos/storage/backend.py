"""StorageBackend abstraction (BB-1) + LocalFSBackend impl (Z decision)."""
from __future__ import annotations

import hashlib
import re
import shutil
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path

_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class StorageBackend(ABC):
    @abstractmethod
    def put(self, src: Path, namespace: str) -> Path: ...
    @abstractmethod
    def get(self, stored: Path) -> bytes: ...
    @abstractmethod
    def exists(self, stored: Path) -> bool: ...
    @abstractmethod
    def list(self, namespace: str) -> Iterable[Path]: ...
    @abstractmethod
    def delete(self, stored: Path) -> None: ...


class LocalFSBackend(StorageBackend):
    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, src: Path, namespace: str) -> Path:
        self._check_namespace(namespace)
        data = src.read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:8]
        safe = _SANITIZE_RE.sub("_", src.name)
        target_dir = self._root / namespace
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest}-{safe}"
        if not target.exists():
            shutil.copyfile(src, target)
        return target

    def get(self, stored: Path) -> bytes:
        self._check_inside(stored)
        return stored.read_bytes()

    def exists(self, stored: Path) -> bool:
        return stored.exists() and self._is_inside(stored)

    def list(self, namespace: str) -> Iterable[Path]:
        self._check_namespace(namespace)
        d = self._root / namespace
        if not d.exists():
            return []
        return [p for p in d.iterdir() if p.is_file()]

    def delete(self, stored: Path) -> None:
        self._check_inside(stored)
        stored.unlink(missing_ok=True)

    def _check_namespace(self, namespace: str) -> None:
        if not _NAMESPACE_RE.fullmatch(namespace):
            raise ValueError(f"invalid namespace: {namespace!r}")

    def _is_inside(self, p: Path) -> bool:
        try:
            Path(p).resolve().relative_to(self._root)
            return True
        except ValueError:
            return False

    def _check_inside(self, p: Path) -> None:
        if not self._is_inside(p):
            raise ValueError(f"path outside storage root: {p}")
