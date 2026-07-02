"""跨进程单飞锁(spec §8): 进程死亡自动释放。
诚实边界: 同主机(MCP stdio + 本地 CLI 的部署现实); PG 多主机 v2 换 advisory lock。

平台分派(Windows 阶段2 spec 附录A): POSIX 用 fcntl.flock(原实现逐字不动),
Windows 用 filelock.WindowsFileLock(msvcrt 硬锁, 不用 SoftFileLock —— 实测
filelock.FileLock 在 fcntl 返 ENOSYS 时会静默 fallback 成 SoftFileLock, 破坏
"进程死自动释放"契约)。公开接口 try_lock(lockfile) -> Iterator[bool] 两分支
形态相同, 2 个直接调用方(init/orchestrator / projection/rebuild_entry)零改动
(CLI / MCP / watcher 经 incremental_rebuild_code 间接复用, 同样零改)。
`import fcntl` 守卫进非-win32 分支, Windows 永不 import 到它(消 ModuleNotFoundError)。
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

if sys.platform == "win32":
    from filelock import Timeout, WindowsFileLock

    @contextmanager
    def try_lock(lockfile: Path) -> Iterator[bool]:
        """非阻塞抢锁。yield True=拿到(退出 with 释放); False=别人持有(调用方应返 already_running)。"""
        lockfile.parent.mkdir(parents=True, exist_ok=True)
        lock = WindowsFileLock(str(lockfile))     # msvcrt 硬锁; 非 SoftFileLock
        try:
            lock.acquire(timeout=0)               # 非阻塞: 被占抛 Timeout
        except Timeout:
            yield False
            return
        try:
            yield True
        finally:
            lock.release()
else:
    import fcntl                                   # 守卫: 仅非 win32; Windows 永不执行到

    @contextmanager
    def try_lock(lockfile: Path) -> Iterator[bool]:
        """非阻塞抢锁。yield True=拿到(退出 with 释放); False=别人持有(调用方应返 already_running)。"""
        lockfile.parent.mkdir(parents=True, exist_ok=True)
        f = open(lockfile, "a+")
        try:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()
