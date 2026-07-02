"""Plan 04b T14: MCP 接线测试(投影 searcher / lookup_calls / read_symbol / 探针 / rebuild)。

设计思路
--------
T14 把 MCP server 的代码维从 live JDT 切到 code_* 持久投影(spec D3 查询期投影-only):
1. AppContext.searcher 换成 ProjectionSearcher(engine)—— 秒级零 JDT;JdtlsAdapter
   构造保留为 jdt_adapter(仅 build 期消费),serve 路径不触发。
2. evidence.py 新增两个 tool:lookup_calls(查 code_calls 边,caps 从 profile 取)/
   read_symbol(按 FQN 切源码,四护栏:FQN-only / resolve 前缀校验 / cap / 脱敏)。
3. middleware 加 FQN 校验(fqn/method_fqn 参数必须像 Java FQN,拒路径穿越/超长)。
4. meta.health_check 加 code_projection 探针(not_built / ok+build_id+indexed_commit);
   incremental_rebuild 实装 code scope(flock 单飞,already_running 不排队)。

评分标准
--------
- search_code:投影空 -> ToolError 含 "contextos init"(诚实 miss);种行后命中
  com.acme.* 返 target/kind/score。
- lookup_calls:返边;depth=5 被 cap 到 profile 上限 2 不报错;direction 非法 ToolError。
- read_symbol:命中切片 + 行内凭据(user/pass@ 形态)打码 + redacted=True;
  fqn="../etc/passwd" 被 middleware 拒("invalid fqn");fqn 超长(>512)拒。
- health_check:未建 -> code_projection.status=="not_built";种 meta 后 ok+build_id。
- incremental_rebuild:先持锁再调 -> already_running;scope="rag" -> not_implemented;
  rebuild_entry 在 run_incremental 返 full_rebuild_required 时同一持锁块内接全量
  (R4),透传 full_rebuild_executed + trigger。
- 真 AppContext.searcher 是 ProjectionSearcher 且不构造 JDT(JdtlsAdapter 替成 boom
  类也不炸);jdt_adapter 惰性不在 __dict__。

自动脚本逻辑
------------
_ProjAppCtx = AppContext duck-typed 替身:真内存 SQLite + ensure_projection_schema,
种中性投影行(com.acme.OrderService + submit/audit 方法 + 2 条 call 边),源码文件落
tmp repo(profile.projects[0].path 指过去),sha1 对齐 code_files(stale=False)。
searcher = 真 ProjectionSearcher(engine)。build_server(ctx) 后经 in-memory Client
走完整 MCP 协议(middleware 自动生效)。fixture 全中性合成名,不掺真客户标识
(守 feedback_offline_test_neutral_fixtures)。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from contextos.code_intel.projection import schema as proj_schema
from contextos.code_intel.projection import store as proj_store
from contextos.code_intel.projection.searcher import ProjectionSearcher
from contextos.mcp_server.server import build_server
from contextos.storage.flock import try_lock

_SRC_REL = "src/main/java/com/acme/OrderService.java"

# 行内凭据(user/pass@ 形态, config_dim.sensitive._CREDS_IN_VALUE 命中)—— 中性合成值。
_JAVA_SOURCE = """\
package com.acme;

public class OrderService {
    private String dsn = "appuser/apppw@ACMEDB";
    public void submit() {
        audit();
    }
    void audit() {
    }
}
"""


def _list_payload(res: Any) -> list[Any]:
    """list 返回型 tool 的真数据(FastMCP list[Any] 元素在 structured_content)。"""
    return res.structured_content["result"]


def _projection_engine() -> Any:
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    proj_schema.ensure_projection_schema(engine)
    return engine


def _seed_projection(engine: Any, repo: Path) -> None:
    """种中性投影行 + 真源码文件(sha1 对齐 -> read_symbol stale=False)。"""
    src = repo / _SRC_REL
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(_JAVA_SOURCE, encoding="utf-8")
    sha1 = hashlib.sha1(src.read_bytes()).hexdigest()
    with engine.begin() as c:
        c.execute(proj_schema.code_files.insert(), [
            {"file_path": _SRC_REL, "lang": "java", "module": "merged",
             "package_name": "com.acme", "sha1": sha1}])
        c.execute(proj_schema.code_classes.insert(), [
            {"class_id": "C1", "lang": "java", "class_fqn": "com.acme.OrderService",
             "class_name": "OrderService", "name_lower": "orderservice",
             "package_name": "com.acme", "source_file": _SRC_REL, "kind": "class",
             "start_line": 2, "end_line": 9}])
        c.execute(proj_schema.code_methods.insert(), [
            {"method_id": "M1", "lang": "java", "class_fqn": "com.acme.OrderService",
             "method_name": "submit", "name_lower": "submit",
             "method_fqn": "com.acme.OrderService.submit()", "is_constructor": 0,
             "source_file": _SRC_REL, "start_line": 4, "end_line": 6},
            {"method_id": "M2", "lang": "java", "class_fqn": "com.acme.OrderService",
             "method_name": "audit", "name_lower": "audit",
             "method_fqn": "com.acme.OrderService.audit()", "is_constructor": 0,
             "source_file": _SRC_REL, "start_line": 7, "end_line": 8}])
        c.execute(proj_schema.code_calls.insert(), [
            {"call_id": "X1", "lang": "java",
             "caller_method_fqn": "com.acme.OrderService.submit()",
             "callee_class_fqn": "com.acme.OrderService", "callee_method_name": "audit",
             "callee_method_fqn": "com.acme.OrderService.audit()",
             "source_file": _SRC_REL, "line_no": 5, "resolved": 1},
            {"call_id": "X2", "lang": "java",
             "caller_method_fqn": "com.acme.OrderService.audit()",
             "callee_class_fqn": "com.acme.util.Log", "callee_method_name": "write",
             "callee_method_fqn": "com.acme.util.Log.write()",
             "source_file": _SRC_REL, "line_no": 8, "resolved": 0}])
    proj_store.set_meta(engine, "projection_build_id", "buildid12345")
    proj_store.set_meta(engine, "last_indexed_commit", "cafebabe1234")
    proj_store.set_meta(engine, "build_status", "ok")


class _ProjAppCtx:
    """AppContext duck-typed 替身:真投影 engine + 真 ProjectionSearcher。
    rag/oracle 不在本 task 测试面(rag_provider 占位,router/querier 离线 None)。
    """

    def __init__(self, profile: Any, engine: Any) -> None:
        self.profile = profile
        self.engine = engine
        self.searcher = ProjectionSearcher(engine)
        self.rag_provider = None

    def oracle_querier(self) -> None:
        return None

    def oracle_router(self) -> None:
        return None

    @property
    def projection_lockfile(self) -> Path:
        return Path(self.profile.storage.data_dir).expanduser() / "projection.lock"


@pytest.fixture
def built_ctx(make_profile, tmp_path: Path) -> _ProjAppCtx:
    """投影已 build:种行 + meta;profile 项目根指向 tmp repo(read_symbol 切真文件)。"""
    profile = make_profile(data_dir=tmp_path / "data")
    repo = tmp_path / "repo"
    repo.mkdir()
    profile.projects[0].path = str(repo)
    engine = _projection_engine()
    _seed_projection(engine, repo)
    return _ProjAppCtx(profile, engine)


@pytest.fixture
def empty_ctx(make_profile, tmp_path: Path) -> _ProjAppCtx:
    """投影未 build:schema 在但无 projection_build_id meta(诚实 miss 分支)。"""
    profile = make_profile(data_dir=tmp_path / "data")
    repo = tmp_path / "repo"
    repo.mkdir()
    profile.projects[0].path = str(repo)
    return _ProjAppCtx(profile, _projection_engine())


@pytest.fixture
def built_server(built_ctx):
    return build_server(built_ctx)


@pytest.fixture
def empty_server(empty_ctx):
    return build_server(empty_ctx)


# --------------------------------------------------------------------- search_code


async def test_search_code_not_built_is_honest_miss(empty_server):
    """投影未 build -> ProjectionMissingError -> ToolError 含修复动作 contextos init。"""
    async with Client(empty_server) as client:
        with pytest.raises(Exception, match="contextos init"):
            await client.call_tool("search_code", {"query": "OrderService"})


async def test_search_code_hits_projection(built_server):
    """种行后命中:target=FQN / kind=CLASS / score>0(零 JDT, 纯查表)。"""
    async with Client(built_server) as client:
        res = await client.call_tool("search_code", {"query": "OrderService"})
        rows = _list_payload(res)
        assert rows and rows[0]["target"] == "com.acme.OrderService"
        assert rows[0]["kind"] == "CLASS"
        assert rows[0]["score"] > 0


# --------------------------------------------------------------------- lookup_calls


async def test_lookup_calls_returns_edges(built_server):
    async with Client(built_server) as client:
        res = await client.call_tool(
            "lookup_calls", {"method_fqn": "com.acme.OrderService.submit()"})
        assert res.data["direction"] == "callees"
        assert res.data["depth"] == 1
        edges = res.data["edges"]
        assert len(edges) == 1
        assert edges[0]["callee_method_fqn"] == "com.acme.OrderService.audit()"


async def test_lookup_calls_depth_capped_to_profile_limit(built_server):
    """depth=5 不报错, 被 cap 到 profile lookup_calls_max_depth=2(两跳拿到 2 条边)。"""
    async with Client(built_server) as client:
        res = await client.call_tool(
            "lookup_calls",
            {"method_fqn": "com.acme.OrderService.submit()", "depth": 5})
        assert res.data["depth"] == 2
        assert len(res.data["edges"]) == 2


async def test_lookup_calls_bad_direction_tool_error(built_server):
    """core ValueError(direction 非法)-> ToolError(不裸传 traceback)。"""
    async with Client(built_server) as client:
        with pytest.raises(Exception, match="direction"):
            await client.call_tool(
                "lookup_calls",
                {"method_fqn": "com.acme.OrderService.submit()",
                 "direction": "sideways"})


# --------------------------------------------------------------------- read_symbol


async def test_read_symbol_slices_and_redacts(built_server):
    """命中切片 + 行内凭据 user/pass@ 形态打码(护栏 4)+ sha1 对齐 stale=False。"""
    async with Client(built_server) as client:
        res = await client.call_tool("read_symbol", {"fqn": "com.acme.OrderService"})
        assert res.data["fqn"] == "com.acme.OrderService"
        assert res.data["file"] == _SRC_REL
        assert "class OrderService" in res.data["source"]
        assert "apppw" not in res.data["source"]          # 凭据片段被打码
        assert "****@" in res.data["source"]              # 拓扑保留, 凭据掩码
        assert res.data["redacted"] is True
        assert res.data["stale"] is False


async def test_read_symbol_path_traversal_rejected_by_middleware(built_server):
    """fqn 不像 Java FQN(路径穿越形态)-> middleware 早拒, tool body 不执行。"""
    async with Client(built_server) as client:
        with pytest.raises(Exception, match="invalid fqn"):
            await client.call_tool("read_symbol", {"fqn": "../etc/passwd"})


async def test_read_symbol_overlong_fqn_rejected_by_middleware(built_server):
    async with Client(built_server) as client:
        with pytest.raises(Exception, match="invalid fqn"):
            await client.call_tool("read_symbol", {"fqn": "a" * 600})


async def test_read_symbol_unknown_fqn_tool_error(built_server):
    """合法形态但投影里没有 -> SymbolNotFound -> ToolError(诚实 miss)。"""
    async with Client(built_server) as client:
        with pytest.raises(Exception, match="not in projection"):
            await client.call_tool("read_symbol", {"fqn": "com.acme.Nope"})


async def test_read_symbol_ambiguous_bare_fqn_lists_candidates(built_ctx, built_server):
    """裸 FQN 命中多重载 -> 专属 AmbiguousMethodFqn 分支(非通用 'read_symbol failed:'
    包装), ToolError 消息列出全部带签名候选。"""
    with built_ctx.engine.begin() as c:
        c.execute(proj_schema.code_methods.insert(), [
            {"method_id": "M3", "lang": "java", "class_fqn": "com.acme.OrderService",
             "method_name": "audit", "name_lower": "audit",
             "method_fqn": "com.acme.OrderService.audit(int)", "is_constructor": 0,
             "source_file": _SRC_REL, "start_line": 7, "end_line": 8}])
    async with Client(built_server) as client:
        with pytest.raises(Exception) as ei:
            await client.call_tool(
                "read_symbol", {"fqn": "com.acme.OrderService.audit"})
    msg = str(ei.value)
    assert "com.acme.OrderService.audit()" in msg
    assert "com.acme.OrderService.audit(int)" in msg
    assert "pass a signature-qualified FQN" in msg
    assert "read_symbol failed:" not in msg        # 专属分支, 不是通用兜底包装


async def test_lookup_calls_method_fqn_also_fqn_validated(built_server):
    """method_fqn 参数同样过 middleware FQN 校验(拒 shell 元字符注入形态)。"""
    async with Client(built_server) as client:
        with pytest.raises(Exception, match="invalid method_fqn"):
            await client.call_tool("lookup_calls", {"method_fqn": "x; rm -rf /"})


async def test_lookup_calls_ambiguous_bare_seed_lists_candidates(built_ctx, built_server):
    """lookup_calls 裸 seed 命中多重载 -> 专属 AmbiguousMethodFqn 分支(非通用
    'lookup_calls failed:' 包装), ToolError 消息列出全部带签名候选。"""
    with built_ctx.engine.begin() as c:
        c.execute(proj_schema.code_methods.insert(), [
            {"method_id": "M3", "lang": "java", "class_fqn": "com.acme.OrderService",
             "method_name": "audit", "name_lower": "audit",
             "method_fqn": "com.acme.OrderService.audit(int)", "is_constructor": 0,
             "source_file": _SRC_REL, "start_line": 7, "end_line": 8}])
    async with Client(built_server) as client:
        with pytest.raises(Exception) as ei:
            await client.call_tool(
                "lookup_calls", {"method_fqn": "com.acme.OrderService.audit"})
    msg = str(ei.value)
    assert "com.acme.OrderService.audit()" in msg
    assert "com.acme.OrderService.audit(int)" in msg
    assert "pass a signature-qualified FQN" in msg
    assert "lookup_calls failed:" not in msg       # 专属分支, 不是通用兜底包装


# --------------------------------------------------------------------- health_check


async def test_health_check_projection_not_built(empty_server):
    async with Client(empty_server) as client:
        res = await client.call_tool("health_check", {})
        probe = res.data["code_projection"]
        assert probe["status"] == "not_built"
        assert "contextos init" in probe["hint"]


async def test_health_check_projection_built(built_server):
    async with Client(built_server) as client:
        res = await client.call_tool("health_check", {})
        probe = res.data["code_projection"]
        assert probe["status"] == "ok"
        assert probe["build_id"] == "buildid12345"
        assert probe["indexed_commit"] == "cafebabe1234"


# --------------------------------------------------------------------- incremental_rebuild


async def test_incremental_rebuild_already_running(built_ctx, built_server):
    """测试先持锁再经 Client 调 -> already_running(spec §8 不排队阻塞)。"""
    lockfile = built_ctx.projection_lockfile
    with try_lock(lockfile) as got:
        assert got
        async with Client(built_server) as client:
            res = await client.call_tool("incremental_rebuild", {"scope": "code"})
            assert res.data == {"scope": "code", "status": "already_running"}


async def test_incremental_rebuild_other_scope_not_implemented(built_server):
    """非 code/all scope 仍占位(其余维度增量 v1.x)。"""
    async with Client(built_server) as client:
        res = await client.call_tool("incremental_rebuild", {"scope": "rag"})
        assert res.data == {"status": "not_implemented", "scope": "rag"}


# --------------------------------------------------------------------- rebuild_entry(R4)


def test_rebuild_entry_chains_full_rebuild_in_same_lock(built_ctx, monkeypatch):
    """R4: run_incremental 返 full_rebuild_required -> 同一持锁块内接 build_projection
    (sampler=None, indexed_commit=build 启动前 HEAD), 透传 full_rebuild_executed。"""
    import contextos.code_intel.projection.rebuild_entry as re_mod

    seen: dict[str, Any] = {}

    def _fake_incremental(**kw: Any) -> dict[str, Any]:
        return {"status": "full_rebuild_required", "detail": "777 files (threshold 500)"}

    def _fake_full(**kw: Any) -> dict[str, Any]:
        seen["full_kwargs"] = kw
        return {"status": "ok", "build_id": "fullb1", "counts": {}}

    monkeypatch.setattr(re_mod, "run_incremental", _fake_incremental)
    monkeypatch.setattr(re_mod, "build_projection", _fake_full)
    monkeypatch.setattr(re_mod, "head_commit_real", lambda repo: "headsha99")

    res = re_mod.incremental_rebuild_code(
        built_ctx.profile, built_ctx.engine, lockfile=built_ctx.projection_lockfile)
    assert res["status"] == "ok"
    assert res["full_rebuild_executed"] is True
    assert res["trigger"] == "777 files (threshold 500)"
    assert seen["full_kwargs"]["indexed_commit"] == "headsha99"   # build 启动前 HEAD
    assert seen["full_kwargs"]["sampler"] is None                 # 无 live JDT 对照


def test_rebuild_entry_fingerprint_change_executes_full(built_ctx, monkeypatch, tmp_path):
    """HIGH-1 契约(最终 review): 指纹变更(换 jar 字节)-> rebuild_entry 同锁内全量被执行
    (full_rebuild_executed=True)。run_incremental 真跑(不 monkeypatch, 走指纹闸),
    只替 build_projection 计数 —— 证明闸门接通了 rebuild_entry 的自动全量, 不是只返信号。"""
    import platform

    import contextos.code_intel.projection.rebuild_entry as re_mod
    from contextos.code_intel.projection import store as proj_store
    from contextos.code_intel.projection.build_context import (
        build_context_dict, context_fingerprint)
    from contextos.code_intel.projection.indexer_runner import jar_fingerprint

    jar = tmp_path / "indexer.jar"
    jar.write_bytes(b"PK-old")
    built_ctx.profile.code_index.indexer_jar = str(jar)
    eng = built_ctx.engine
    # 入档"上次 build"指纹: ctx / jdk 与当前一致, jar 一致(随后换字节制造唯一 diff)
    proj_store.set_meta(eng, "jar_hash", jar_fingerprint(jar))
    proj_store.set_meta(eng, "build_context_hash",
                        context_fingerprint(build_context_dict(built_ctx.profile)))
    proj_store.set_meta(
        eng, "jdk_fingerprint",
        f"{built_ctx.profile.jdtls_runtime.java_home}|{platform.machine()}")
    jar.write_bytes(b"PK-new-swapped")            # 换 jar

    calls = {"full": 0}

    def _fake_full(**kw: Any) -> dict[str, Any]:
        calls["full"] += 1
        return {"status": "ok", "build_id": "fp1", "counts": {}}

    monkeypatch.setattr(re_mod, "build_projection", _fake_full)
    res = re_mod.incremental_rebuild_code(
        built_ctx.profile, eng, lockfile=built_ctx.projection_lockfile)
    assert calls["full"] == 1                      # 全量被执行
    assert res["full_rebuild_executed"] is True
    assert "fingerprint" in res["trigger"]


def test_rebuild_entry_passes_through_incremental_ok(built_ctx, monkeypatch):
    """非 full_rebuild_required 直接透传, 不触发全量。"""
    import contextos.code_intel.projection.rebuild_entry as re_mod

    monkeypatch.setattr(
        re_mod, "run_incremental",
        lambda **kw: {"status": "ok", "detail": "", "reparsed": 3})

    def _boom(**kw: Any) -> dict[str, Any]:
        raise AssertionError("full rebuild must not run on incremental ok")

    monkeypatch.setattr(re_mod, "build_projection", _boom)
    res = re_mod.incremental_rebuild_code(
        built_ctx.profile, built_ctx.engine, lockfile=built_ctx.projection_lockfile)
    assert res == {"status": "ok", "detail": "", "reparsed": 3}


# --------------------------------------------------------------------- AppContext 不触发 JDT


def test_app_context_searcher_is_projection_no_jdt(make_profile, monkeypatch) -> None:
    """真 AppContext.searcher = ProjectionSearcher(秒级零 JDT, spec D3)。
    JdtlsAdapter 替成 boom 类也不炸 = 证明查询路径完全不构造 JDT;
    jdt_adapter 惰性(cached_property 不访问不构造)。"""
    import contextos.mcp_server.app_context as appctx_mod
    from contextos.mcp_server.app_context import AppContext

    class _BoomAdapter:
        def __init__(self, **_kw: object) -> None:
            raise AssertionError("JDT must not be constructed on serve path")

    monkeypatch.setattr(appctx_mod, "JdtlsAdapter", _BoomAdapter)
    ctx = AppContext.from_profile(make_profile())
    assert isinstance(ctx.searcher, ProjectionSearcher)
    assert "jdt_adapter" not in ctx.__dict__       # 惰性, serve 路径不触发


def test_app_context_has_no_prewarm(make_profile) -> None:
    """prewarm_searcher 已删(无 JDT 可预热;投影查询本来就秒级)。"""
    from contextos.mcp_server.app_context import AppContext

    ctx = AppContext.from_profile(make_profile())
    assert not hasattr(ctx, "prewarm_searcher")


def test_app_context_projection_lockfile(make_profile, tmp_path: Path) -> None:
    from contextos.mcp_server.app_context import AppContext

    ctx = AppContext.from_profile(make_profile(data_dir=tmp_path / "dd"))
    assert ctx.projection_lockfile == tmp_path / "dd" / "projection.lock"
