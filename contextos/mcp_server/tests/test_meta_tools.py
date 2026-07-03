"""3 元工具的 MCP 包装 + impl 测试(Plan 10 Task 8)。

设计思路
--------
mcp_server/tools/meta.py 暴露 3 个元工具(health_check / profile_info /
incremental_rebuild)+ register_meta_tools(mcp, app_ctx) 注册进 server。本层是薄
包装:探测 app_ctx 已有资源的状态(不强行起 JDT 冷启 / 不强行构造需凭据的 llm)、
读 profile 的非敏感元信息、给增量 rebuild 留安全占位。

三个工具的契约(plan Task 8 / spec §4.3):
1. health_check_impl(app_ctx) -> {jdt_ls, oracle, models, engine}。每个探测 try/except
   不抛;jdt_ls/models 用"资源是否已 materialized(cached_property 落进 __dict__)"判
   cold/lazy vs ready —— **不**触发 JDT ~196s 冷启,也**不**强行构造离线缺凭据会抛的 llm;
   oracle 用 app_ctx.oracle_router().fan_out() 是否非空判 connected/offline(Block 1b);engine 轻量 SELECT 1
   探活。
2. profile_info_impl(app_ctx) -> {profile_path, data_dir, oracle_instances, rag_corpora,
   missing_required}。**脱敏铁律(红线 #9 host 不可信 + 凭据绝不外泄)**:只列实例名 /
   corpus 名 / 字段名 / 路径,**绝不回显任何凭据值**(password/api_key/secret/token 的明文)。
   profile.llm.api_key_env 是环境变量**名**(指针, 非密钥本体)—— 即便它出现也不是泄漏,
   但本工具策略是**白名单输出**(只产被显式选中的非敏感字段),不做整 profile dump,从结构上
   杜绝凭据值进入返回。
3. incremental_rebuild_impl(app_ctx, *, scope):code/all scope 已实装(Plan 04b T14,
   走 rebuild_entry + flock 单飞;契约测试在 test_projection_tools.py);其余 scope
   (rag/lineage/config...)仍占位 {status:"not_implemented", scope}(维度增量 v1.x)。

本测试核心验证
--------------
- health_check 在 querier=None(离线)时返 oracle:"offline" 不崩;jdt_ls 在 searcher 未起时
  返 "cold"(不触发冷启);models 在 llm 未构造时返 "lazy";engine "ok"。
- health_check 在 searcher/llm 已 materialized 时返 "ready"。
- **profile_info 输出不含任何凭据明文值**:构造一个 profile, 用一个会被
  os.environ[api_key_env] 命中的真值塞进环境(模拟 host 试图诱导回显),断言返回里
  既无该密钥明文、也无任何 password/secret/token 明文值;同时正确列出实例名 / corpus 名。
- incremental_rebuild 非 code scope 返 {status:"not_implemented", scope}(code 见
  test_projection_tools.py)。
- 经 in-memory Client 调 3 个 MCP tool 均可达(注册生效)。

评分标准
--------
- 3 impl 各返契约 dict;health_check 离线/未起资源不崩, 状态字段语义对。
- profile_info 脱敏: repr(返回) 不含任何凭据明文(密钥真值 / password / secret / token)。
- 3 tool 经 register_meta_tools + build_server 后 in-memory Client 可调。
- fixture 用中性合成值(APP/feature.flag.x / FEATURE_DOCS), 不掺真客户名。

自动脚本逻辑
------------
_MetaAppCtx = AppContext duck-typed 替身:profile 用 make_profile 合成(注入
corpus_subset_prefixes + 一个凭据 env 名), engine 用真内存 SQLite(SELECT 1 可探活),
oracle_router()->None(离线, Block 1b)。两个变体:资源未 materialized(默认)/ 已 materialized
(把 llm/searcher 提前塞进 __dict__ 模拟"被用过")。
"""
from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from contextos.mcp_server.tools.meta import (
    health_check_impl,
    incremental_rebuild_impl,
    profile_info_impl,
    register_meta_tools,
)

@pytest.fixture(autouse=True)
def _no_real_vscode_scan(monkeypatch):
    """hermetic 守卫: 本文件的合成 profile 路径(/jdtls 等)不存在, jdtls_runtime 探针会
    走 missing 分支真扫开发机 ~/.vscode/extensions 与 <cwd>/runtime/contextos-runtime
    (spec C1 bundle 支路)—— 结果依赖跑测机器。两条探测都钉死为"探不到",
    探测逻辑本身在 test_discovery.py / test_health_jdtls_probe.py 单独测。"""
    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(D, "discover_vscode_jdtls", lambda home=None: None)
    monkeypatch.setattr(D, "discover_runtime_bundle",
                        lambda repo=None, platform_config=None: None)


# host 试图诱导回显的"凭据明文":塞进 os.environ[<api_key_env>] + 自由文本, 验证绝不外泄。
_FAKE_API_KEY_VALUE = "sk-LEAKME-secret-key-9999"
_FAKE_PASSWORD_VALUE = "p@ssw0rd-LEAKME"


def _list_payload(res: Any) -> list[Any]:
    """list 返回型 tool 的真数据(FastMCP list[Any] 元素在 structured_content)。"""
    return res.structured_content["result"]


class _FakeLLMMarker:
    """已 materialized 的 llm 占位(只为让 'llm' 落进 __dict__, 不需可调)。"""


class _FakeSearcherMarker:
    """已 materialized 的 searcher 占位。"""


class _MetaAppCtx:
    """AppContext duck-typed 替身(meta 工具只读 profile + 探测资源 materialized 状态)。

    - profile: make_profile 合成 + 注入 corpus_subset_prefixes(rag_corpora 来源)+
      api_key_env 指向一个我们在环境里塞了真值的变量名(脱敏验证靶子)。
    - engine: 真内存 SQLite, health_check 的 SELECT 1 探活可过。
    - oracle_querier(): None(旧接口保留)。oracle_router(): None(离线分支, Block 1b)。
    - materialized: 控制是否预先把 llm/searcher 塞进 __dict__(模拟"已被用过")。
    """

    def __init__(self, profile: Any, *, materialized: bool = False) -> None:
        from sqlalchemy import create_engine

        self.profile = profile
        self._engine = create_engine("sqlite://")
        if materialized:
            # cached_property 缓存键 = 实例 __dict__[<name>];预置即"已构造"。
            self.__dict__["llm"] = _FakeLLMMarker()
            self.__dict__["searcher"] = _FakeSearcherMarker()

    @property
    def engine(self) -> Any:
        # 不走 cached_property(避免与 materialized 探测冲突);直接返真 engine。
        return self._engine

    def oracle_querier(self) -> None:
        # 保留旧接口供其他 duck-type 调用者;health_check 已改用 oracle_router(Block 1b)。
        return None

    def oracle_router(self) -> None:
        # 离线分支:router=None -> fan_out=[] -> oracle:'offline'(Block 1b Task 14)。
        return None


@pytest.fixture
def meta_profile(make_profile):
    """合成 profile + 注入 corpus 子集 + 凭据 env 名 + 环境里塞密钥真值。"""
    profile = make_profile()
    # rag_corpora 来源:03 §2.1 已注册 corpus 子集 = corpus_subset_prefixes 的键。
    profile.config.corpus_subset_prefixes = {
        "feature_docs": ["docs/feature"],
        "ops_runbook": ["docs/ops"],
    }
    return profile


@pytest.fixture
def meta_app_ctx(meta_profile):
    return _MetaAppCtx(meta_profile)


@pytest.fixture
def meta_server(meta_app_ctx):
    """build_server(已注册 build_impact_map + 13 证据 tool)再注册 3 元工具。"""
    from contextos.mcp_server.server import build_server

    return build_server(meta_app_ctx)


# --------------------------------------------------------------------------- health_check


def test_health_check_offline_does_not_crash(meta_app_ctx) -> None:
    """router=None(离线)-> oracle:'offline';searcher/llm 未起 -> cold/lazy;engine ok。"""
    h = health_check_impl(meta_app_ctx)
    assert h["oracle"] == "offline"            # router=None 离线, 不崩(Block 1b)
    assert h["jdt_ls"] == "cold"               # searcher 未 materialized, 不触发冷启
    assert h["models"] == "lazy"               # llm 未构造(离线缺凭据也不强构造)
    assert h["engine"] == "ok"                 # 内存 SQLite SELECT 1 探活过


def test_health_check_ready_when_resources_materialized(meta_profile) -> None:
    """searcher/llm 已 materialized(被用过)-> jdt_ls:'ready' / models:'ready'。"""
    ctx = _MetaAppCtx(meta_profile, materialized=True)
    h = health_check_impl(ctx)
    assert h["jdt_ls"] == "ready"
    assert h["models"] == "ready"


def test_health_check_engine_error_is_caught() -> None:
    """engine 探活抛异常 -> 不冒泡, engine 字段标 error(探测 try/except)。"""

    class _BoomCtx:
        profile = None

        @property
        def engine(self) -> Any:
            raise RuntimeError("db unreachable")

        def oracle_querier(self) -> None:
            return None

        def oracle_router(self) -> None:
            # 离线分支(Block 1b Task 14)。
            return None

    h = health_check_impl(_BoomCtx())
    assert h["engine"].startswith("error")     # 探测捕获, 不抛
    assert h["oracle"] == "offline"


# --------------------------------------------------------------------------- profile_info


def test_profile_info_basic_shape(meta_app_ctx) -> None:
    info = profile_info_impl(meta_app_ctx)
    assert set(info.keys()) >= {
        "profile_path", "data_dir", "repo_root", "source_roots",
        "oracle_instances", "rag_corpora", "missing_required",
    }
    # 列实例名(TNS 名, 非凭据)
    assert info["oracle_instances"] == ["TEST_DB1"]
    # 列 corpus 子集名(键), 不泄 prefix 细节为值
    assert set(info["rag_corpora"]) == {"feature_docs", "ops_runbook"}
    assert isinstance(info["missing_required"], list)


def _profile_with_source_roots(make_profile, source_roots):
    """基于中性 make_profile 造一个带 code.source_roots 的变体(projects[0].path='/proj')。"""
    base = make_profile()
    return base.model_copy(
        update={"code": base.code.model_copy(update={"source_roots": source_roots})}
    )


def test_profile_info_empty_source_roots_falls_back_to_repo(make_profile) -> None:
    p = _profile_with_source_roots(make_profile, [])
    info = profile_info_impl(types.SimpleNamespace(profile=p))
    assert info["repo_root"] == str(Path("/proj").resolve())
    assert info["source_roots"] == [str(Path("/proj").resolve())]


def test_profile_info_relative_source_roots_resolved_under_repo(make_profile) -> None:
    p = _profile_with_source_roots(make_profile, ["soa/src"])
    info = profile_info_impl(types.SimpleNamespace(profile=p))
    assert info["source_roots"] == [str((Path("/proj") / "soa/src").resolve())]


def test_profile_info_absolute_source_roots_kept(make_profile) -> None:
    p = _profile_with_source_roots(make_profile, ["/ext/code"])
    info = profile_info_impl(types.SimpleNamespace(profile=p))
    assert info["source_roots"] == [str(Path("/ext/code").resolve())]


def test_profile_info_redacts_credentials(meta_profile, monkeypatch) -> None:
    """脱敏铁律:host 即便把密钥真值塞进环境 / 自由文本, profile_info 也绝不回显凭据明文。

    做法:把 profile.llm.api_key_env 指向 MCP_TEST_LLM_KEY, 并在环境里给它塞真密钥;
    同时往一个会被 dump 的字段塞 password 形态明文。断言返回的 repr 里:
      - 无该密钥真值(_FAKE_API_KEY_VALUE)
      - 无任何 password 明文(_FAKE_PASSWORD_VALUE)
    白名单输出从结构上保证(只产选中字段, 不整 profile dump)。
    """
    # 环境里塞密钥真值(模拟 host 已配 key);api_key_env 名指向它。
    monkeypatch.setenv(meta_profile.llm.api_key_env, _FAKE_API_KEY_VALUE)
    # 往 framework_annotations(会进 dump 风险面的自由 list)塞一个 password 形态明文,
    # 验证即便 profile 里有凭据形态字符串, 白名单输出也不会带出来。
    meta_profile.config.framework_annotations = [
        f"@Secret(value={_FAKE_PASSWORD_VALUE})"
    ]
    ctx = _MetaAppCtx(meta_profile)
    info = profile_info_impl(ctx)
    blob = repr(info)
    assert _FAKE_API_KEY_VALUE not in blob     # 密钥真值绝不外泄
    assert _FAKE_PASSWORD_VALUE not in blob     # password 明文绝不外泄
    assert "password" not in blob.lower()       # 连 password 字样都不该出现在输出里
    # 同时仍正常列出非敏感元信息
    assert info["oracle_instances"] == ["TEST_DB1"]


def test_profile_info_api_key_env_name_is_pointer_not_secret(meta_app_ctx) -> None:
    """即便输出包含 api_key_env(环境变量名), 那是指针不是密钥本体;关键是无明文值。

    本测试不要求 api_key_env 名一定出现(白名单可不含它), 只确保:若出现, 也只是名字,
    且环境里对应的真值绝不出现。
    """
    import os

    os.environ.setdefault("MCP_TEST_LLM_KEY", "")  # 确保读名不读值
    info = profile_info_impl(meta_app_ctx)
    blob = repr(info)
    assert _FAKE_API_KEY_VALUE not in blob


# --------------------------------------------------------------------------- incremental_rebuild


def test_incremental_rebuild_is_safe_placeholder_for_other_scopes(meta_app_ctx) -> None:
    """Plan 04b T14: code/all scope 已实装(见 test_projection_tools.py);
    其余维度(rag/lineage/config...)增量 v1.x 仍占位 not_implemented。"""
    r = incremental_rebuild_impl(meta_app_ctx, scope="rag")
    assert r["status"] == "not_implemented"
    assert r["scope"] == "rag"


def test_incremental_rebuild_echoes_scope(meta_app_ctx) -> None:
    r = incremental_rebuild_impl(meta_app_ctx, scope="config")
    assert r["status"] == "not_implemented"
    assert r["scope"] == "config"


# --------------------------------------------------------------------------- MCP wiring


async def test_meta_tools_registered_and_callable(meta_server) -> None:
    """3 元工具经 register_meta_tools + build_server 后 in-memory Client 可达。"""
    async with Client(meta_server) as client:
        h = await client.call_tool("health_check", {})
        assert h.data["oracle"] == "offline"

        info = await client.call_tool("profile_info", {})
        assert info.data["oracle_instances"] == ["TEST_DB1"]

        reb = await client.call_tool("incremental_rebuild", {"scope": "lineage"})
        assert reb.data["status"] == "not_implemented"
        assert reb.data["scope"] == "lineage"


async def test_meta_tools_listed(meta_server) -> None:
    """3 元工具出现在 server 工具清单里(注册可见)。"""
    async with Client(meta_server) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert {"health_check", "profile_info", "incremental_rebuild"} <= names


async def test_profile_info_via_client_no_credentials(meta_profile, monkeypatch) -> None:
    """经 MCP Client 调 profile_info 同样不泄凭据(端到端脱敏)。"""
    monkeypatch.setenv(meta_profile.llm.api_key_env, _FAKE_API_KEY_VALUE)
    # duck-typed AppContext 替身(对齐 conftest._FakeAppCtx 范式;build_server 只 duck-type
    # 用 app_ctx 的属性, 不要求 isinstance AppContext)。Any 绑定避免 pyright reportArgumentType。
    ctx: Any = _MetaAppCtx(meta_profile)
    from contextos.mcp_server.server import build_server

    server = build_server(ctx)
    async with Client(server) as client:
        info = await client.call_tool("profile_info", {})
        blob = repr(info.data)
        assert _FAKE_API_KEY_VALUE not in blob
        assert _FAKE_PASSWORD_VALUE not in blob


# --------------------------------------------------------------------------- Task 6: ripgrep probe + census patterns


import shutil as _shutil


class _MetaCtx:
    """最小 duck-type ctx(Task 6 单测用): profile + oracle_router + engine(抛错)。
    engine 抛错 -> _probe_engine 捕获返 'error:...'(不冒泡), 不影响 ripgrep 键断言。"""

    def __init__(self, profile: Any) -> None:
        self.profile = profile

    def oracle_router(self) -> None:
        return None

    @property
    def engine(self) -> Any:
        raise RuntimeError("no engine in this unit ctx")


def test_health_check_has_ripgrep_key(make_profile) -> None:
    """health_check 返回 dict 包含 'ripgrep' 键, 值为 'ok' 或 'missing'。"""
    from contextos.mcp_server.tools.meta import health_check_impl
    ctx = _MetaCtx(make_profile())
    out = health_check_impl(ctx)
    assert "ripgrep" in out
    assert out["ripgrep"] in ("ok", "missing")


def test_probe_ripgrep_matches_environment(make_profile) -> None:
    """_probe_ripgrep 返回值与当前环境 rg 是否在 PATH 一致。"""
    from contextos.mcp_server.tools.meta import _probe_ripgrep
    expected = "ok" if _shutil.which("rg") else "missing"
    assert _probe_ripgrep(_MetaCtx(make_profile())) == expected


def test_profile_info_exposes_census_patterns(make_profile) -> None:
    """profile_info 暴露 dispatch_patterns / carrier_read_patterns(非 write-only)。"""
    from contextos.mcp_server.tools.meta import profile_info_impl
    profile = make_profile()
    profile.code.dispatch_patterns = ["FrameworkDispatcher.callByName"]
    profile.code.carrier_read_patterns = ["StaticDict.getList"]
    out = profile_info_impl(_MetaCtx(profile))
    assert out["dispatch_patterns"] == ["FrameworkDispatcher.callByName"]
    assert out["carrier_read_patterns"] == ["StaticDict.getList"]


# --------------------------------------------------------------------------- Task 7: 站点7/8 bytes helper


@pytest.mark.cmd_boundary
def test_probe_ripgrep_ok_when_rg_present():
    """站点7: rg 在 PATH -> _probe_ripgrep 返 'ok'(走 run_rg --version, bytes)。"""
    import shutil
    if shutil.which("rg") is None:
        import pytest
        pytest.skip("rg not installed")
    from contextos.mcp_server.tools.meta import _probe_ripgrep
    assert _probe_ripgrep(object()) == "ok"


@pytest.mark.cmd_boundary
def test_probe_code_projection_commits_behind_parses(tmp_path, make_profile, monkeypatch):
    """站点8: git rev-list --count 经 run_git bytes -> int(decode_content(...).strip()) 解析正确。
    用真 git 仓造 1 commit 落后, 断 commits_behind == 1。"""
    import shutil
    if shutil.which("git") is None:
        import pytest
        pytest.skip("git not installed")
    import subprocess as _sp
    from contextos.code_intel.projection import store as proj_store
    from contextos.mcp_server.tools.meta import _probe_code_projection

    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.txt").write_text("1\n", encoding="utf-8")
    env = {**__import__("os").environ}
    _sp.run(["git", "-C", str(repo), "init", "-q"], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.email", "t@t.t"], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    _sp.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    _sp.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True, env=env)
    base = _sp.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                   capture_output=True, text=True).stdout.strip()
    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    _sp.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    _sp.run(["git", "-C", str(repo), "commit", "-qm", "c2"], check=True, env=env)

    profile = make_profile()        # 工厂 fixture: data_dir 是 keyword-only, 不可位置传参(本测试不依赖 profile)

    class _Ctx:
        pass

    ctx = _Ctx()
    from sqlalchemy import create_engine
    ctx.engine = create_engine("sqlite:///:memory:")
    ctx.profile = profile
    from contextos.code_intel.projection import schema as _S
    _S.ensure_projection_schema(ctx.engine)
    proj_store.set_meta(ctx.engine, "projection_build_id", "b1")
    proj_store.set_meta(ctx.engine, "last_indexed_commit", base)
    # repo_root(profile) 指向我们的临时 git 仓
    monkeypatch.setattr("contextos.code_intel.projection.paths.repo_root", lambda _p: repo)

    out = _probe_code_projection(ctx)
    assert out["commits_behind"] == 1
