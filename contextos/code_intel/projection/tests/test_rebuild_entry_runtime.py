"""review P1 契约: rebuild_entry 的 java_home 必须来自 resolver(经 from_profile),
不许直读 profile.jdtls_runtime.java_home —— 否则占位 profile 下 JDT 半边回退成功,
indexer 半边仍拿占位路径崩。
真入口 = incremental_rebuild_code(profile, engine, *, lockfile)。"""
from contextlib import contextmanager
from typing import cast

from sqlalchemy.engine import Engine


def test_rebuild_entry_java_home_goes_through_resolver(tmp_path, monkeypatch):
    import contextos.code_intel.projection.rebuild_entry as re_mod

    captured = {}

    def fake_run_incremental(**kw):
        captured["java_home"] = kw["java_home"]
        return {"status": "noop"}

    @contextmanager
    def fake_lock(_path):
        yield True

    # run_incremental/try_lock/head_commit_real/build_context_dict 均按名 import
    # 进 rebuild_entry 命名空间, monkeypatch re_mod 上的名字即生效
    monkeypatch.setattr(re_mod, "run_incremental", fake_run_incremental)
    monkeypatch.setattr(re_mod, "try_lock", fake_lock)
    monkeypatch.setattr(re_mod, "head_commit_real", lambda repo: "deadbeef")
    monkeypatch.setattr(re_mod, "build_context_dict", lambda p: {})
    sentinel = "/resolved/by/resolver/jre"

    class _FakeRt:
        java_home = sentinel

    monkeypatch.setattr(
        "contextos.code_intel.jdtls_provider.config.JdtlsRuntimeConfig.from_profile",
        classmethod(lambda cls, p: _FakeRt()))

    (tmp_path / "idx.jar").write_bytes(b"x")   # indexer resolver: profile 值存在即用

    class _Proj:
        path = str(tmp_path); name = "p"

    class _Code:
        source_roots: list = []; exclude_dirs: list = []

    class _Storage:
        data_dir = str(tmp_path / "data")

    class _Ci:
        indexer_jar = str(tmp_path / "idx.jar")
        indexer_xmx = "1g"; incremental_max_files = 500

    class _Rt:
        jdtls_path, lombok_path, java_home = "/raw/a", "/raw/b", "/raw/c"

    class _Profile:
        projects = [_Proj()]; code = _Code(); storage = _Storage()
        code_index = _Ci(); jdtls_runtime = _Rt()

    r = re_mod.incremental_rebuild_code(
        _Profile(), engine=cast(Engine, object()), lockfile=tmp_path / "projection.lock")
    assert r == {"status": "noop"}
    assert captured["java_home"] == sentinel      # 直读会拿到 /raw/c -> 必须是 sentinel


def test_rebuild_entry_full_branch_java_home_goes_through_resolver(tmp_path, monkeypatch):
    """R4(spec §5.3): run_incremental 返回 full_rebuild_required 时同一持锁块内接
    build_projection 跑全量 —— 该分支的 java_home 同样必须来自 resolver, 不许悄悄
    退回直读(两条调用路径都吃同一个 rt, 但要各自断言防"改一半漏一半")。"""
    import contextos.code_intel.projection.rebuild_entry as re_mod

    def fake_run_incremental(**kw):
        return {"status": "full_rebuild_required", "detail": "x"}

    @contextmanager
    def fake_lock(_path):
        yield True

    seen = {}

    def fake_build_projection(**kw):
        seen.update(kw)
        return {"status": "ok"}

    monkeypatch.setattr(re_mod, "run_incremental", fake_run_incremental)
    monkeypatch.setattr(re_mod, "try_lock", fake_lock)
    monkeypatch.setattr(re_mod, "head_commit_real", lambda repo: "deadbeef")
    monkeypatch.setattr(re_mod, "build_context_dict", lambda p: {})
    monkeypatch.setattr(re_mod, "build_projection", fake_build_projection)
    sentinel = "/resolved/by/resolver/jre-full"

    class _FakeRt:
        java_home = sentinel

    monkeypatch.setattr(
        "contextos.code_intel.jdtls_provider.config.JdtlsRuntimeConfig.from_profile",
        classmethod(lambda cls, p: _FakeRt()))

    (tmp_path / "idx.jar").write_bytes(b"x")

    class _Proj:
        path = str(tmp_path); name = "p"

    class _Code:
        source_roots: list = []; exclude_dirs: list = []

    class _Storage:
        data_dir = str(tmp_path / "data")

    class _Ci:
        indexer_jar = str(tmp_path / "idx.jar")
        indexer_xmx = "1g"; incremental_max_files = 500

    class _Rt:
        jdtls_path, lombok_path, java_home = "/raw/a", "/raw/b", "/raw/c"

    class _Profile:
        projects = [_Proj()]; code = _Code(); storage = _Storage()
        code_index = _Ci(); jdtls_runtime = _Rt()

    r = re_mod.incremental_rebuild_code(
        _Profile(), engine=cast(Engine, object()), lockfile=tmp_path / "projection.lock")
    assert seen["java_home"] == sentinel          # 直读会拿到 /raw/c -> 必须是 sentinel
    assert r == {"status": "ok", "full_rebuild_executed": True, "trigger": "x"}
