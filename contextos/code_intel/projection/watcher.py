"""文件 watcher(spec §5.3 常驻层): watchdog Observer -> Debouncer -> 增量重建。

- 防抖: 最后一个事件后静默 quiet_seconds 才 flush(git checkout 风暴聚成一批,
  超 max_files 由 run_incremental 阈值 -> rebuild_entry 全量兜底)
- 降级: 原生 Observer start 失败 -> watchdog PollingObserver
- 启动补课由 serve 入口另起线程调 rebuild_entry 一次, watcher 只管运行期
- flush 失败吞异常记日志(watcher 不死), 下批事件再试
- flush 批内容只当触发信号; 真变更集由 run_incremental 两层检测算(单一事实源)
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


def _normalize_event_path(p: str, repo: Path) -> str | None:
    """watcher 事件路径归一(Windows 阶段2 spec 附录B): 仓内事件 -> 正斜杠相对锚;
    仓外事件(source root 在仓外时触发的 fs 事件)-> None, 调用方丢弃。这是
    既有跨平台行为(relative_to 对仓外路径本就抛 ValueError), 不是本阶段
    引入的新限制 —— 本阶段只把 in-repo 分支的分隔符归一, 不扩 watcher 功能。"""
    try:
        return Path(p).resolve().relative_to(repo).as_posix()
    except ValueError:
        return None


class Debouncer:
    """纯逻辑可测核(时钟/回调注入)。线程封装在 ProjectionWatcher。"""

    def __init__(self, *, quiet_seconds: float, on_flush: Callable[[list[str]], None],
                 now: Callable[[], float] = time.monotonic,
                 exclude_dirs: list[str] | None = None) -> None:
        self._quiet = quiet_seconds
        self._on_flush = on_flush
        self._now = now
        self._exclude = exclude_dirs or []
        self._pending: set[str] = set()
        self._last_event = 0.0
        self._lock = threading.Lock()

    def offer(self, rel_path: str) -> None:
        if not rel_path.endswith(".java"):
            return
        parts = rel_path.split("/")
        if any(d in parts for d in self._exclude):
            return
        with self._lock:
            self._pending.add(rel_path)
            self._last_event = self._now()

    def poll(self) -> bool:
        """静默期已到则 flush。返回是否 flush 了(watcher 线程周期调)。"""
        with self._lock:
            if not self._pending or self._now() - self._last_event < self._quiet:
                return False
            batch = sorted(self._pending)
            self._pending.clear()
        try:
            self._on_flush(batch)
        except Exception:
            log.exception("projection incremental flush failed (watcher stays alive)")
        return True


class ProjectionWatcher:
    """watchdog 接线 + poll 线程。"""

    def __init__(self, *, source_roots: list[Path], repo_root: Path,
                 debouncer: Debouncer, poll_interval: float = 0.5) -> None:
        self._roots = source_roots
        self._repo = repo_root
        self._deb = debouncer
        self._interval = poll_interval
        self._stop = threading.Event()
        self._observer: Any = None
        self._poll_thread: threading.Thread | None = None

    def start(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
        from watchdog.observers.polling import PollingObserver

        deb, repo = self._deb, self._repo

        class _H(FileSystemEventHandler):
            def on_any_event(self, event: Any) -> None:  # created/modified/deleted/moved 全收
                for p in filter(None, [getattr(event, "src_path", None),
                                       getattr(event, "dest_path", None)]):
                    rel = _normalize_event_path(str(p), repo)
                    if rel is not None:
                        deb.offer(rel)

        for cls in (Observer, PollingObserver):   # 原生失败降级 polling(spec §5.3)
            backend = getattr(cls, "__name__", str(cls))  # ObserverType 别名无 __name__ 静态类型
            try:
                obs = cls()
                for r in self._roots:
                    if r.is_dir():
                        obs.schedule(_H(), str(r), recursive=True)
                obs.daemon = True
                obs.start()
                self._observer = obs
                log.info("projection watcher started (%s)", backend)
                break
            except Exception:
                log.exception("watcher backend %s failed, trying fallback", backend)

        t = threading.Thread(target=self._loop, name="projection-watch-poll", daemon=True)
        t.start()
        self._poll_thread = t

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self._deb.poll()

    def stop(self) -> None:
        """完整关停: stop 之外必须 join(merge-review HIGH: FSEvents 原生线程在解释器
        拆卸期还在投递事件 -> segfault/bus error 竞态; join 把拆卸排到事件流之后)。"""
        self._stop.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5.0)
            except Exception:
                log.exception("observer shutdown failed (continuing)")
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5.0)


def start_projection_watch(app_ctx: Any) -> ProjectionWatcher | None:
    """serve 入口一站式: watcher(若启用)+ 启动补课线程。返回 watcher 或 None。"""
    from contextos.code_intel.projection.paths import repo_root, resolve_source_roots
    from contextos.code_intel.projection.rebuild_entry import incremental_rebuild_code

    profile = app_ctx.profile

    def _rebuild_once() -> None:
        # 返回值(dict)丢弃: watcher/补课只当触发器, 结果状态由 rebuild_entry 自记日志。
        incremental_rebuild_code(
            profile, app_ctx.engine, lockfile=app_ctx.projection_lockfile)

    threading.Thread(    # 启动补课(spec §5.3): 停机期变更, 后台一次, server 不等
        target=_rebuild_once, name="projection-catchup", daemon=True).start()
    if not profile.code_index.watcher_enabled:
        return None
    repo = repo_root(profile)
    deb = Debouncer(
        quiet_seconds=profile.code_index.watcher_debounce_seconds,
        on_flush=lambda _batch: _rebuild_once(),
        exclude_dirs=list(profile.code.exclude_dirs))
    w = ProjectionWatcher(source_roots=resolve_source_roots(profile), repo_root=repo,
                          debouncer=deb)
    w.start()
    return w
