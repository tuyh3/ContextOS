"""flock 单飞: 同进程二次抢 -> False(NB 不阻塞); 释放后可再抢; 真双进程互斥。
Windows 阶段2(spec 附录A): flock.py 内部按平台分派(POSIX fcntl 原样 / Windows
WindowsFileLock), 公开接口 try_lock 不变。跨进程测试改走 try_lock 接口(不再
硬写 fcntl)——两平台都能跑同一份子进程代码; 新增 POSIX 洁净断言。
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from contextos.storage.flock import try_lock

_CHILD_SCRIPT = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "from contextos.storage.flock import try_lock\n"
    "with try_lock(Path(sys.argv[1])) as got:\n"
    "    print('locked' if got else 'notlocked', flush=True)\n"
    "    time.sleep(10)\n"
)


def test_acquire_and_contend(tmp_path):
    lockfile = tmp_path / "x.lock"
    with try_lock(lockfile) as got:
        assert got is True
        # 第二个持有者(独立 fd, 模拟另一进程)拿不到
        with try_lock(lockfile) as got2:
            assert got2 is False
    # 释放后能再拿
    with try_lock(lockfile) as got3:
        assert got3 is True


def test_cross_process_contention(tmp_path):
    """真双进程: 子进程持锁期间, 本进程抢不到; 子进程退出后能拿到。
    子进程改走 try_lock 接口(不硬写 fcntl)——同一份代码两平台都覆盖。"""
    lockfile = tmp_path / "x.lock"
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD_SCRIPT, str(lockfile)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"   # 子进程已持锁
        with try_lock(lockfile) as got:
            assert got is False                              # 跨进程抢不到
    finally:
        child.kill()
        child.wait()
    # 子进程死了锁自动释放。Windows 上 TerminateProcess 后句柄回收/字节区锁释放相对
    # wait() 返回是异步的(sub-10ms 滞后), timeout=0 单次即抢可能撞进拆解窗口; POSIX
    # 首次即成(循环退化为 no-op)。轮询容忍该窗口, 不改产品单飞语义。
    got = False
    for _ in range(50):                                      # 最多 ~5s
        with try_lock(lockfile) as got:
            if got:
                break
        time.sleep(0.1)
    assert got is True


def test_lockfile_parent_created(tmp_path):
    lockfile = tmp_path / "deep" / "nested" / "x.lock"
    with try_lock(lockfile) as got:
        assert got is True


def test_posix_branch_has_no_filelock_reference():
    """spec §7 建议: 非 win32 下 flock 模块内部不引用 filelock(POSIX 洁净)。
    用模块属性断言而非 sys.modules —— sys.modules 会被其它依赖间接 import
    filelock 污染, 证不了 flock.py 自身没 import(同款教训见 Phase 1:
    fcntl in sys.modules 曾被 subprocess 的平台分支污染)。"""
    if sys.platform == "win32":
        pytest.skip("此断言只对 POSIX 分支有意义")
    import contextos.storage.flock as flock_mod
    assert not hasattr(flock_mod, "WindowsFileLock")
    assert not hasattr(flock_mod, "Timeout")
    assert hasattr(flock_mod, "fcntl")
