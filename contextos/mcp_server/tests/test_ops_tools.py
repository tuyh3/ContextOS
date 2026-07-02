"""record_confirmed_case MCP tool + strict scope 不回退全量验收(spec Appendix C/E)。

设计思路: register_ops_tools 把 record_confirmed_case 注册成 MCP tool(异常转 ToolError)。
build_server 启动时 ensure_confirmed_cases_dir 建空目录;空目录时 rag_search(corpora=
["confirmed-cases"]) 返回空(miss), 不回退搜全量根(关键: 否则污染成搜全量业务文档)。
评分标准:
  [MCP 可达] in-memory Client 调 record_confirmed_case 成功返 case_id。
  [不回退全量] 空 confirmed-cases 目录 + 业务文档在别的子目录 -> rag_search(["confirmed-cases"])
              不返回业务文档(strict scope, 不回退全量根)。
  [init 建目录] ensure_confirmed_cases_dir 被 build_server 调用 -> 目录存在。
  [路径同口径] profile 自定义 materialized_dir 含 ~ 时, 写入(recorder)与检索(rag_provider)
              走同一 resolver(spec Appendix C MUST), 都展开 ~ -> 同目录; strict scope 仍守住,
              不因写入展开/检索字面分叉而把 confirmed-cases prefix 漏掉回退搜全量根。
自动脚本逻辑: 真 AppContext.from_profile(中性 profile, tmp data_dir), build_server, FastMCP
in-memory Client 调用。strict scope 直接经 rag_tool.rag_search 验证。[路径同口径] 用 monkeypatch
HOME 把 ~ 展开到 tmp 目录(POSIX .expanduser() 读 HOME), corpus.materialized_dir='~/...'。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from contextos.mcp_server.app_context import AppContext
from contextos.mcp_server.server import build_server
from contextos.ops import paths
from contextos.profile.schema import Profile


def _profile(tmp_path: Path) -> Profile:
    return Profile(**{
        "llm": {"provider": "t", "api_key_env": "OPS_K"},
        "embedding": {"model": "m"},
        "reranker": {"enabled": False, "model": "r"},
        "query_expansion": {"enabled": True, "translation_provider": "a",
                            "fallback_provider": "b"},
        "storage": {"data_dir": str(tmp_path / "dd")},
        "ingestion": {"default_cleanup": "full", "chunk_strategy": "h2_h3",
                      "min_chunk_chars": 30},
        "rag": {"reranker_backend": "fake"},
        "jdtls_runtime": {"jdtls_path": "/j", "lombok_path": "/l", "java_home": "/h"},
        "oracle": {"tns_admin": "/t", "allowed_instances": ["TEST_DB1"]},
        "projects": [{"name": "p", "path": "/p", "language": "java"}],
    })


def test_build_server_creates_empty_confirmed_cases_dir(tmp_path):
    app_ctx = AppContext.from_profile(_profile(tmp_path))
    build_server(app_ctx)
    assert paths.confirmed_cases_dir(app_ctx.profile).is_dir()


@pytest.mark.asyncio
async def test_record_confirmed_case_mcp_reachable(tmp_path):
    from fastmcp import Client
    app_ctx = AppContext.from_profile(_profile(tmp_path))
    mcp = build_server(app_ctx)
    async with Client(mcp) as client:
        res = await client.call_tool("record_confirmed_case", {
            "phenomenon_signature": "信用额度内订购大额套餐成功",
            "search_terms": "递延收费 余额不足",
            "behavior_class": "扣费",
            "confirmed_root_cause": "递延收费 时点解耦",
            "mechanism_tag": "deferred_charge",
            "evidence_pointers": ["fqn:com.example.Foo.bar"],
            "confirmed_by_role": "expert",
            "source_type": "manual",
        })
    data = res.data if hasattr(res, "data") else res
    assert "case_id" in (data or {})


def test_empty_corpus_does_not_fall_back_to_full(tmp_path):
    """spec Appendix E [不回退全量]: confirmed-cases 空目录 -> 检索不污染成搜全量业务文档。"""
    from contextos.recall.rag_tool import rag_search
    profile = _profile(tmp_path)
    app_ctx = AppContext.from_profile(profile)
    build_server(app_ctx)   # 触发 ensure_confirmed_cases_dir 建空目录

    mat = paths.resolved_materialized_dir(profile)
    # 在别的子目录放一篇会被关键词命中的业务文档(模拟全量根有内容)
    biz = mat / "biz-docs"
    biz.mkdir(parents=True, exist_ok=True)
    (biz / "doc.md").write_text("递延收费 是一种计费模式", encoding="utf-8")

    rows = rag_search(
        app_ctx.rag_provider,
        queries={"zh": "递延收费", "en": ""},
        corpora=["confirmed-cases"],
        corpus_prefixes=profile.config.corpus_subset_prefixes,
    )
    # confirmed-cases 空 -> 不应返回 biz-docs 的命中(否则就是回退搜了全量根)
    assert all("biz-docs" not in r["doc"] for r in rows), \
        f"strict scope 失效, 回退搜了全量根: {rows}"


def test_tilde_materialized_dir_write_and_search_same_resolver(tmp_path, monkeypatch):
    """[路径同口径] spec Appendix C MUST: profile 自定义 materialized_dir 含 ~ 时,
    写入(recorder)与检索(rag_provider)走同一 resolver(都展开 ~), 不分叉 ->
    confirmed-cases prefix 永远有效, strict scope 不回退全量根污染业务文档。

    回归点: 旧实现 paths.resolved_materialized_dir 展开 ~、AppContext.rag_provider /
    RagProvider 不展开 -> 写入落 <HOME>/.../confirmed-cases、检索查字面 '~/.../confirmed-cases'
    (.exists() 假阴) -> path_prefixes 丢失 -> ripgrep 搜全量根 -> 污染业务文档。
    """
    from contextos.recall.rag_tool import rag_search

    # 跨平台重定向 ~: POSIX expanduser 读 HOME; Windows ntpath.expanduser 读 USERPROFILE
    # (退回 HOMEDRIVE+HOMEPATH), 不读 HOME。都指到 tmp 才隔离。
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))            # POSIX
    monkeypatch.setenv("USERPROFILE", str(fake_home))     # Windows 首选

    profile = _profile(tmp_path)
    profile.corpus.materialized_dir = "~/ops-mat"   # 含 ~, 触发 expanduser 分支
    app_ctx = AppContext.from_profile(profile)
    build_server(app_ctx)   # 触发 ensure_confirmed_cases_dir 建空目录(写入侧 resolver)

    # 写入侧(recorder/paths)与检索侧(rag_provider)resolver 必须落到同一展开目录
    resolved = paths.resolved_materialized_dir(profile)
    assert resolved == fake_home / "ops-mat", "~ 未展开到 fake HOME"
    assert app_ctx.rag_provider._dir == resolved, \
        f"检索 resolver 与写入 resolver 分叉: {app_ctx.rag_provider._dir} != {resolved}"
    assert paths.confirmed_cases_dir(profile).is_dir(), "空 confirmed-cases 目录未建在展开路径下"

    # 在展开根的别的子目录放会被关键词命中的业务文档(模拟全量根有内容)
    biz = resolved / "biz-docs"
    biz.mkdir(parents=True, exist_ok=True)
    (biz / "doc.md").write_text("递延收费 是一种计费模式", encoding="utf-8")

    rows = rag_search(
        app_ctx.rag_provider,
        queries={"zh": "递延收费", "en": ""},
        corpora=["confirmed-cases"],
        corpus_prefixes=profile.config.corpus_subset_prefixes,
    )
    # 若写入/检索分叉, confirmed-cases prefix 字面 .exists() 假阴 -> 回退搜全量根 ->
    # 命中 biz-docs。同口径修复后 confirmed-cases 空 -> miss, 绝不返回 biz-docs。
    assert all("biz-docs" not in r["doc"] for r in rows), \
        f"strict scope 失效(写入/检索 resolver 分叉, 回退搜全量根): {rows}"
