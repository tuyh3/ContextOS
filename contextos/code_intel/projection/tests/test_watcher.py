"""watcher 防抖核心(可注入时钟+回调, 不起真 watchdog): 事件聚 2s 静默后 flush 一次 /
非 .java 与 exclude 目录事件被滤 / flush 回调异常不杀线程(记日志继续)。
真 watchdog Observer 接线归 integration(T18 手测), 单测只测 Debouncer。

start_projection_watch 集成测(轻量, 不碰真 rebuild): monkeypatch
rebuild_entry.incremental_rebuild_code(watcher 模块函数内 import, 打源模块即生效),
fake app_ctx 用 SimpleNamespace。验 watcher_enabled=False -> 返回 None 但补课线程仍跑 /
watcher_enabled=True -> 返回 ProjectionWatcher 且 stop() 不抛。真 fs 事件归 T18。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

from contextos.code_intel.projection.watcher import (
    Debouncer,
    ProjectionWatcher,
    start_projection_watch,
)


def test_debounce_coalesces():
    fired: list[set[str]] = []
    clock = [0.0]
    d = Debouncer(quiet_seconds=2.0, on_flush=lambda fs: fired.append(set(fs)),
                  now=lambda: clock[0])
    d.offer("src/A.java")
    clock[0] = 1.0
    d.offer("src/B.java")
    assert d.poll() is False        # 还没静默 2s
    clock[0] = 3.1                  # 距最后事件 2.1s
    assert d.poll() is True
    assert fired == [{"src/A.java", "src/B.java"}]
    assert d.poll() is False        # 清空后不重复 flush


def test_filter_java_and_excludes():
    fired: list[set[str]] = []
    d = Debouncer(quiet_seconds=0.0, on_flush=lambda fs: fired.append(set(fs)),
                  now=lambda: 0.0, exclude_dirs=["build"])
    d.offer("src/A.txt")            # 非 .java
    d.offer("x/build/G.java")       # exclude 目录
    d.offer("src/OK.java")
    d.poll()
    assert fired == [{"src/OK.java"}]


def test_flush_exception_swallowed():
    def boom(_):
        raise RuntimeError("rebuild failed")
    d = Debouncer(quiet_seconds=0.0, on_flush=boom, now=lambda: 0.0)
    d.offer("src/A.java")
    assert d.poll() is True         # 异常被吞(记日志), 线程不死


def test_quiet_window_resets_on_new_event():
    fired: list[set[str]] = []
    clock = [0.0]
    d = Debouncer(quiet_seconds=2.0, on_flush=lambda fs: fired.append(set(fs)),
                  now=lambda: clock[0])
    d.offer("src/A.java")
    clock[0] = 1.9
    d.offer("src/B.java")           # 重置静默窗
    clock[0] = 3.0                  # 距最后事件只 1.1s
    assert d.poll() is False
    clock[0] = 4.0                  # 2.1s
    assert d.poll() is True


# --------------------------------------------------------------------------
# start_projection_watch 集成(fake app_ctx + monkeypatch rebuild)
# --------------------------------------------------------------------------

def _fake_app_ctx(tmp_path, *, watcher_enabled: bool) -> Any:
    profile = SimpleNamespace(
        projects=[SimpleNamespace(path=str(tmp_path))],
        code=SimpleNamespace(source_roots=[], exclude_dirs=["build"]),
        code_index=SimpleNamespace(
            watcher_enabled=watcher_enabled, watcher_debounce_seconds=2.0),
    )
    return SimpleNamespace(
        profile=profile, engine=object(),
        projection_lockfile=tmp_path / "projection.lock")


def test_start_disabled_returns_none_but_catchup_runs(tmp_path, monkeypatch):
    """watcher_enabled=False: 不起 watcher(None), 启动补课线程仍跑一次 rebuild。"""
    import contextos.code_intel.projection.rebuild_entry as rebuild_entry

    called = threading.Event()

    def fake_rebuild(profile, engine, *, lockfile):
        called.set()
        return {"status": "ok"}

    monkeypatch.setattr(rebuild_entry, "incremental_rebuild_code", fake_rebuild)
    w = start_projection_watch(_fake_app_ctx(tmp_path, watcher_enabled=False))
    assert w is None
    assert called.wait(timeout=5.0), "startup catchup thread never called rebuild"


class _FakeObserver:
    """watchdog Observer 替身(merge-review HIGH: unit test 不起真 native FSEvents ——
    原生线程在解释器拆卸期投递事件会 segfault/bus error, 环境敏感地炸全套 pytest)。
    真 fs 事件链路归 T18 真环境(serve-mcp 冒烟已真启过)。"""

    instances: "list[_FakeObserver]" = []

    def __init__(self) -> None:
        self.scheduled: list = []
        self.started = False
        self.stopped = False
        self.joined = False
        self.daemon = False
        _FakeObserver.instances.append(self)

    def schedule(self, handler, path, recursive=False):
        self.scheduled.append((handler, path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True


def test_start_enabled_returns_watcher_and_stop_joins(tmp_path, monkeypatch):
    """watcher_enabled=True: 返回 ProjectionWatcher; stop() 必须 observer.stop+join
    且 poll 线程 join 后真死(HIGH: 不 join = native 拆卸竞态)。observer 用替身。"""
    import contextos.code_intel.projection.rebuild_entry as rebuild_entry

    called = threading.Event()

    def fake_rebuild(profile, engine, *, lockfile):
        called.set()
        return {"status": "ok"}

    monkeypatch.setattr(rebuild_entry, "incremental_rebuild_code", fake_rebuild)
    _FakeObserver.instances.clear()
    monkeypatch.setattr("watchdog.observers.Observer", _FakeObserver)
    monkeypatch.setattr("watchdog.observers.polling.PollingObserver", _FakeObserver)
    w = start_projection_watch(_fake_app_ctx(tmp_path, watcher_enabled=True))
    try:
        assert isinstance(w, ProjectionWatcher)
        assert called.wait(timeout=5.0), "startup catchup thread never called rebuild"
        assert _FakeObserver.instances and _FakeObserver.instances[0].started
    finally:
        assert w is not None
        w.stop()
    obs = _FakeObserver.instances[0]
    assert obs.stopped and obs.joined                      # observer 完整关停
    assert w._poll_thread is not None
    assert not w._poll_thread.is_alive()                   # poll 线程 join 后真死


def test_native_observer_failure_falls_back_to_polling(tmp_path, monkeypatch):
    """原生 Observer 起不来 -> PollingObserver 降级(spec §5.3), 全程替身。"""
    import contextos.code_intel.projection.rebuild_entry as rebuild_entry

    monkeypatch.setattr(rebuild_entry, "incremental_rebuild_code",
                        lambda profile, engine, *, lockfile: {"status": "ok"})

    class _BoomObserver(_FakeObserver):
        def start(self):
            raise RuntimeError("native backend unavailable")

    _FakeObserver.instances.clear()
    monkeypatch.setattr("watchdog.observers.Observer", _BoomObserver)
    monkeypatch.setattr("watchdog.observers.polling.PollingObserver", _FakeObserver)
    w = start_projection_watch(_fake_app_ctx(tmp_path, watcher_enabled=True))
    try:
        assert isinstance(w, ProjectionWatcher)
        fallback = [o for o in _FakeObserver.instances
                    if type(o) is _FakeObserver and o.started]
        assert fallback, "polling fallback not started"
    finally:
        assert w is not None
        w.stop()


def test_normalize_event_path_in_repo_as_posix(tmp_path):
    """Windows 阶段2 附录B: 仓内事件路径归一为正斜杠相对锚。"""
    from contextos.code_intel.projection.watcher import _normalize_event_path
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    f = repo / "src" / "A.java"
    f.write_text("class A {}", encoding="utf-8")
    assert _normalize_event_path(str(f), repo) == "src/A.java"


def test_normalize_event_path_out_of_repo_dropped(tmp_path):
    """仓外事件路径 -> None(既有丢弃行为, 非 Windows 引入, 本阶段不扩 watcher 功能)。"""
    from contextos.code_intel.projection.watcher import _normalize_event_path
    other = tmp_path / "elsewhere"
    other.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _normalize_event_path(str(other / "X.java"), repo) is None
