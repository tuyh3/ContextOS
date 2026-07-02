"""search_source 核 + 工具单测。rg 真跑(rg 缺失则 skip)。中性合成源码树。

设计思路: 在 tmp 下造 source 树(含 exclude 目录 / 多扩展名 / 超大文件 / 行内伪凭据),
直接调核函数断言: 预选稳定排序 + cap + 超大剔除; 命中 literal/regex; exclude 嵌套排除;
扩展名 allowlist; caps(limit/per-file/files); 仓外根命中不丢; 脱敏; rg 缺失硬抛。
评分标准: 每条断言对应 spec §5 一项验收, 真路径/真 rg 命中, 非 mock。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastmcp import Client
from sqlalchemy import create_engine

from contextos.code_intel.projection import schema as _proj
from contextos.code_intel.source_search import (
    RipgrepUnavailable,
    SearchSourceError,
    _preselect,
    search_source,
)
from contextos.mcp_server.server import build_server

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="rg not installed")


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_preselect_orders_excludes_and_caps(tmp_path: Path):
    repo = tmp_path / "repo"
    _write(repo / "src/b.java", "class B {}\n")
    _write(repo / "src/a.java", "class A {}\n")
    _write(repo / "src/build/Gen.java", "class Gen {}\n")   # exclude_dirs -> 排
    _write(repo / "conf/x.properties", "k=v\n")
    _write(repo / "img/logo.bin", "x\n")                    # 非 allowlist 扩展 -> 排

    pre = _preselect(
        repo_root=repo, source_roots=[repo],
        extensions=[".java", ".properties"], exclude_dirs=["build"],
        max_files_scanned=100, max_bytes_per_file=1_000_000,
    )
    rels = [c.rel_to_root for c in pre.candidates]
    # build/ 嵌套排除 + .bin 非 allowlist 排除
    assert "src/build/Gen.java" not in rels
    assert "img/logo.bin" not in rels
    # 稳定排序: 相对路径升序(a 在 b 前)
    assert rels == sorted(rels)
    assert "src/a.java" in rels and "src/b.java" in rels and "conf/x.properties" in rels
    assert pre.files_scanned == len(pre.candidates)
    assert pre.truncated is False


def test_preselect_caps_files_and_marks_truncated(tmp_path: Path):
    repo = tmp_path / "repo"
    for i in range(5):
        _write(repo / f"src/f{i}.java", "class F {}\n")
    pre = _preselect(
        repo_root=repo, source_roots=[repo],
        extensions=[".java"], exclude_dirs=[],
        max_files_scanned=2, max_bytes_per_file=1_000_000,
    )
    assert pre.files_scanned == 2          # cap 精确
    assert pre.truncated is True           # 候选 > cap -> 覆盖不完整


def test_preselect_drops_oversize_and_marks_truncated(tmp_path: Path):
    repo = tmp_path / "repo"
    _write(repo / "src/small.java", "class S {}\n")
    _write(repo / "src/big.java", "x" * 5000 + "\n")
    pre = _preselect(
        repo_root=repo, source_roots=[repo],
        extensions=[".java"], exclude_dirs=[],
        max_files_scanned=100, max_bytes_per_file=1000,
    )
    rels = [c.rel_to_root for c in pre.candidates]
    assert "src/small.java" in rels
    assert "src/big.java" not in rels      # 超 max_bytes 剔除
    assert pre.truncated is True


def test_preselect_exact_fit_not_truncated(tmp_path: Path):
    # MEDIUM-3: 候选数 == max_files_scanned 是"恰好填满", 不是截断, 不得误降级
    repo = tmp_path / "repo"
    for i in range(2):
        _write(repo / f"src/f{i}.java", "class F {}\n")
    pre = _preselect(
        repo_root=repo, source_roots=[repo],
        extensions=[".java"], exclude_dirs=[],
        max_files_scanned=2, max_bytes_per_file=1_000_000,
    )
    assert pre.files_scanned == 2
    assert pre.truncated is False          # 全收, 无丢弃 -> 不截断


def test_search_literal_hit_text_tier(tmp_path: Path):
    repo = tmp_path / "repo"
    _write(repo / "src/com/example/app/Dispatch.java",
           'class Dispatch {\n  void f() {\n'
           '    FrameworkDispatcher.callByName("svc.someRestrictionCheck");\n  }\n}\n')
    out = search_source(repo_root=repo, source_roots=[repo],
                        query="FrameworkDispatcher.callByName", sensitive_patterns=[])
    assert out["searched_roots"] == [str(repo.resolve())]
    assert out["total_matches"] == 1
    hit = out["results"][0]
    assert hit["path"] == "src/com/example/app/Dispatch.java"
    assert hit["repo_relative"] == "src/com/example/app/Dispatch.java"
    assert hit["line"] == 3
    assert hit["ext"] == ".java"
    assert hit["evidence_tier"] == "text-hit"
    assert hit["enclosing_method_fqn"] is None      # Task 3 无 engine -> 不回填
    assert out["truncated"] is False
    assert out["per_file_truncated"] is False


def test_search_regex_mode(tmp_path: Path):
    repo = tmp_path / "repo"
    _write(repo / "a.java", 'x.startsWith("PREFIX_A");\n')
    out = search_source(repo_root=repo, source_roots=[repo],
                        query=r'startsWith\("PREFIX_', mode="regex", sensitive_patterns=[])
    assert out["total_matches"] == 1


def test_search_redacts_inline_credential(tmp_path: Path):
    repo = tmp_path / "repo"
    # 行内凭据 user/pass@host(中性合成); redact_secrets_in_text 命中 -> ****@
    _write(repo / "C.java", 'String dsn = "appuser/apppw@EXDB";\n')
    out = search_source(repo_root=repo, source_roots=[repo],
                        query="dsn", sensitive_patterns=[])
    snippet = out["results"][0]["snippet"]
    assert "apppw" not in snippet
    assert "****@" in snippet


def test_search_per_file_match_cap(tmp_path: Path):
    repo = tmp_path / "repo"
    _write(repo / "m.java", "TOKEN\n" * 10)
    out = search_source(repo_root=repo, source_roots=[repo],
                        query="TOKEN", sensitive_patterns=[], max_matches_per_file=3)
    assert out["total_matches"] == 3
    assert out["per_file_truncated"] is True


def test_search_total_limit(tmp_path: Path):
    repo = tmp_path / "repo"
    for i in range(4):
        _write(repo / f"f{i}.java", "HIT\n")
    out = search_source(repo_root=repo, source_roots=[repo],
                        query="HIT", sensitive_patterns=[], limit=2)
    assert out["total_matches"] == 2
    assert out["truncated"] is True


def test_search_limit_exact_fit_not_truncated(tmp_path: Path):
    # MEDIUM-1: 命中数恰好 == limit(无丢弃)不得误标 truncated(对称于 max_files exact-fit)
    repo = tmp_path / "repo"
    for i in range(2):
        _write(repo / f"f{i}.java", "HIT\n")
    out = search_source(repo_root=repo, source_roots=[repo],
                        query="HIT", sensitive_patterns=[], limit=2)
    assert out["total_matches"] == 2
    assert out["truncated"] is False


def test_search_external_root_keeps_abs_path_and_null_repo_relative(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    ext_root = tmp_path / "external_center"      # 仓外绝对根
    _write(ext_root / "svc/Ext.java", "callByName_here\n")
    out = search_source(repo_root=repo, source_roots=[ext_root],
                        query="callByName_here", sensitive_patterns=[])
    hit = out["results"][0]
    assert hit["root"] == str(ext_root.resolve())
    assert hit["path"] == "svc/Ext.java"          # 相对该根
    assert hit["repo_relative"] is None           # 仓外 -> null(不强行 relative_to(repo))
    assert str(ext_root.resolve()) in out["searched_roots"]


def test_search_context_lines(tmp_path: Path):
    repo = tmp_path / "repo"
    _write(repo / "x.java", "L1\nL2\nNEEDLE\nL4\nL5\n")
    out = search_source(repo_root=repo, source_roots=[repo],
                        query="NEEDLE", sensitive_patterns=[], context_lines=1)
    snippet = out["results"][0]["snippet"]
    assert "L2" in snippet and "NEEDLE" in snippet and "L4" in snippet


def test_search_rg_missing_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("contextos.code_intel.source_search.shutil.which", lambda _: None)
    with pytest.raises(RipgrepUnavailable):
        search_source(repo_root=tmp_path, source_roots=[tmp_path],
                      query="x", sensitive_patterns=[])


@pytest.mark.parametrize("bad", [["*"], [".*"], ["**/*"], ["ja va"], ["a/b"], ["x{y}"]])
def test_search_rejects_glob_metachar_extension(tmp_path: Path, bad):
    # MEDIUM-1: file_extensions 是 host 入参; 拒 glob 元字符, 防扩成任意 grep(*.* 全扫)
    with pytest.raises(SearchSourceError):
        search_source(repo_root=tmp_path, source_roots=[tmp_path],
                      query="x", sensitive_patterns=[], file_extensions=bad)


def _projection_engine_with_method(repo_relative: str, class_fqn: str,
                                    method_fqn: str, start: int, end: int):
    """内存 sqlite + 投影 schema, 种一行 code_methods(测试用; 非生产存储)。"""
    eng = create_engine("sqlite://")
    _proj.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(_proj.code_methods.insert(), [{
            "lang": "java", "class_fqn": class_fqn, "method_name": "f",
            "name_lower": "f", "method_fqn": method_fqn,
            "source_file": repo_relative, "start_line": start, "end_line": end,
        }])
    return eng


def test_backfill_enclosing_fqn_for_in_projection_java(tmp_path: Path):
    repo = tmp_path / "repo"
    rel = "src/com/example/app/Dispatch.java"
    _write(repo / rel,
           "package com.example.app;\nclass Dispatch {\n  void f() {\n"
           '    FrameworkDispatcher.callByName("svc.check");\n  }\n}\n')
    engine = _projection_engine_with_method(
        rel, "com.example.app.Dispatch",
        "com.example.app.Dispatch.f()", start=3, end=5)
    out = search_source(repo_root=repo, source_roots=[repo],
                        query="callByName", sensitive_patterns=[], engine=engine)
    hit = out["results"][0]
    assert hit["enclosing_class_fqn"] == "com.example.app.Dispatch"
    assert hit["enclosing_method_fqn"] == "com.example.app.Dispatch.f()"


def test_backfill_null_for_non_java(tmp_path: Path):
    repo = tmp_path / "repo"
    _write(repo / "conf/app.properties", "feature.callByName=on\n")
    engine = _projection_engine_with_method(
        "irrelevant.java", "X", "X.f()", 1, 9)
    out = search_source(repo_root=repo, source_roots=[repo],
                        query="callByName", sensitive_patterns=[], engine=engine)
    hit = out["results"][0]
    assert hit["ext"] == ".properties"
    assert hit["enclosing_method_fqn"] is None     # 非 .java -> 不回填


def test_backfill_enclosing_fqn_for_out_of_repo_java(tmp_path: Path):
    # I-1: 仓外 source root 的 .java 命中, repo_relative=None; 投影锚的是绝对路径 posix 串
    # (Windows 阶段2 整族: incremental._scan_source_roots / jsonl_load._rel 仓外分支都
    # as_posix, 不再 str(abs))。_backfill_fqn 须用 abs_path.as_posix() 回退 key, 否则
    # 跨模块(仓外)caller 的 FQN 桥静默失败(Windows 上更会因 '\\' vs '/' 全失配)。
    repo = tmp_path / "repo"
    repo.mkdir()
    ext_root = tmp_path / "external_center"          # 仓外绝对根(非 repo 子目录)
    java = ext_root / "svc/com/example/ext/Caller.java"
    _write(java,
           "package com.example.ext;\nclass Caller {\n  void g() {\n"
           '    FrameworkDispatcher.callByName("svc.check");\n  }\n}\n')
    # 投影 source_file 锚 = 该 .java 的绝对路径 posix 串(out-of-repo 口径, 与生产者同)
    engine = _projection_engine_with_method(
        java.resolve().as_posix(), "com.example.ext.Caller",
        "com.example.ext.Caller.g()", start=3, end=5)
    out = search_source(repo_root=repo, source_roots=[ext_root],
                        query="callByName", sensitive_patterns=[], engine=engine)
    hit = out["results"][0]
    assert hit["repo_relative"] is None              # 仓外 -> null
    assert hit["enclosing_class_fqn"] == "com.example.ext.Caller"
    assert hit["enclosing_method_fqn"] == "com.example.ext.Caller.g()"


# ---------------------------------------------------------------------------
# in-memory Client smoke: 经 MCP 出口端到端(注册 + 脱敏 + searched_roots + text-hit)
# ---------------------------------------------------------------------------

class _SrcAppCtx:
    """AppContext duck-typed 替身(search_source 工具面: profile + engine + 脱敏)。"""
    def __init__(self, profile, engine=None):
        self.profile = profile
        self._engine = engine
        self.rag_provider = None      # 占位: build_server -> register_evidence_tools 注册期不读
    @property
    def engine(self):
        if self._engine is None:
            raise RuntimeError("no engine")
        return self._engine
    def oracle_router(self):
        return None
    def oracle_querier(self):
        return None
    @property
    def searcher(self):
        from contextos.code_intel.projection.searcher import ProjectionSearcher
        return ProjectionSearcher(self.engine)
    @property
    def projection_lockfile(self) -> Path:
        return Path(self.profile.storage.data_dir).expanduser() / "projection.lock"


async def test_search_source_tool_end_to_end(make_profile, tmp_path: Path):
    repo = tmp_path / "repo"
    _write(repo / "C.java", 'String dsn = "appuser/apppw@EXDB"; // callByName\n')
    profile = make_profile(data_dir=tmp_path / "data")
    profile.projects[0].path = str(repo)
    engine = create_engine("sqlite://")
    _proj.metadata.create_all(engine)
    server = build_server(_SrcAppCtx(profile, engine))
    async with Client(server) as client:
        res = await client.call_tool("search_source", {"query": "callByName"})
        data = res.data
        assert data["searched_roots"] == [str(repo.resolve())]
        assert data["total_matches"] == 1
        assert data["results"][0]["evidence_tier"] == "text-hit"
        # 行内凭据脱敏(经 MCP 出口仍打码)
        blob = repr(data)
        assert "apppw" not in blob


async def test_search_source_registered_no_drift(make_profile, tmp_path: Path):
    # Task 9: search_source 注册 + 既有核心工具零漂移
    repo = tmp_path / "repo"
    repo.mkdir()
    profile = make_profile(data_dir=tmp_path / "data")
    profile.projects[0].path = str(repo)
    engine = create_engine("sqlite://")
    _proj.metadata.create_all(engine)
    server = build_server(_SrcAppCtx(profile, engine))
    async with Client(server) as client:
        tools = {t.name for t in await client.list_tools()}
    assert "search_source" in tools
    for t in ("search_code", "read_symbol", "lookup_calls", "search_sql",
              "health_check", "profile_info"):
        assert t in tools


@pytest.mark.cmd_boundary
def test_search_source_content_colon_preserved(tmp_path: Path):
    """内容含冒号(及 Windows 风格盘符样文本)经 NUL+首冒号切后完整保留, 不被 split 切碎。"""
    repo = tmp_path / "repo"
    _write(repo / "src/Cfg.java", 'String url = "jdbc:oracle:thin:@host";\n')
    res = search_source(repo_root=repo, source_roots=[repo], query="jdbc",
                        sensitive_patterns=[], mode="literal")
    snips = [r["snippet"] for r in res["results"]]
    assert any("jdbc:oracle:thin:@host" in s for s in snips)   # 多冒号内容完整


@pytest.mark.cmd_boundary
def test_search_source_crlf_strips_trailing_cr(tmp_path: Path):
    """CRLF 源文件命中 snippet 尾无残留 \\r。"""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "C.java").write_bytes(b'class C { String x = "NEEDLE"; }\r\n')
    res = search_source(repo_root=repo, source_roots=[repo], query="NEEDLE",
                        sensitive_patterns=[], mode="literal", context_lines=0)
    assert res["total_matches"] >= 1
    assert all(not r["snippet"].endswith("\r") for r in res["results"])


@pytest.mark.cmd_boundary
def test_search_source_skips_binary_file_matches_no_false_hit(tmp_path: Path):
    """§7.2 守卫真触发处(review 第七轮 P2): source_search 用**显式文件列表**跑 rg, 含真 NUL 的
    .java 文件命中后 rg 在 stdout 出 'binary file matches'(无真 NUL 字节)行; parser 守卫
    if b'\\0' not in record: continue 必须跳过 —— 不崩(否则 record.split(b'\\0',1) 解包 ValueError)、
    不产假 hit。(sparse 的目录扫描模式 rg 自动静默跳 binary, 不出此行; 唯有显式文件路径才出。)"""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "bin.java").write_bytes(b'class B { String x = "NEEDLE"; }\x00tail NEEDLE\n')
    (repo / "src" / "ok.java").write_text('class K { String y = "NEEDLE"; }\n', encoding="utf-8")
    res = search_source(repo_root=repo, source_roots=[repo], query="NEEDLE",
                        sensitive_patterns=[], mode="literal")     # 不得抛
    assert any("ok.java" in r["path"] for r in res["results"])     # 正常文件命中
    assert all("binary file matches" not in r["snippet"] for r in res["results"])   # 守卫跳过, 无假 hit


@pytest.mark.cmd_boundary
def test_search_source_non_ascii_path_matches(tmp_path: Path):
    """§7.3b 真 rg 非 ASCII smoke(三平台 CI 实证): 中文文件名命中能映射回 by_abs。
    rg --files --null 出 path bytes -> os.fsdecode -> 候选 abs_path; 内容流命中 path
    回吐 -> os.fsdecode 对得上 by_abs 键。POSIX 由 roundtrip 证, Windows 靠本 smoke。"""
    repo = tmp_path / "repo"
    _write(repo / "src" / "中文name.java", 'class C { String x = "NEEDLE"; }\n')
    res = search_source(repo_root=repo, source_roots=[repo], query="NEEDLE",
                        sensitive_patterns=[], mode="literal")
    assert res["total_matches"] >= 1
    assert any("中文name.java" in r["path"] for r in res["results"])


@pytest.mark.cmd_boundary
def test_list_root_files_no_dot_prefix(tmp_path: Path):
    """_list_root_files 返回相对 root 的 posix 路径, 不暴露 ./ 前缀(去掉末尾 '.' 参数即无前缀)。"""
    from contextos.code_intel.source_search import _glob_flags, _list_root_files
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.java").write_text("class A{}\n", encoding="utf-8")
    rels = _list_root_files(tmp_path, _glob_flags([".java"], []))
    assert "src/a.java" in rels
    assert all(not r.startswith("./") for r in rels)


def test_backfill_out_of_repo_anchor_uses_abs_posix_matches_producer(tmp_path: Path):
    """Windows 阶段2 第5锚(review 抓, 整族补): _backfill_fqn 仓外分支 sf 用
    cand.abs_path.as_posix()(原 str(cand.abs_path))—— 与生产者 jsonl_load._rel /
    incremental._scan_source_roots 仓外分支写 code_methods.source_file 的口径一致。

    设计思路: 显式把投影锚**只**种成 as_posix 形态(生产者真写的形态), 且用
    _Candidate(repo_relative=None) 强制走仓外 sf 分支, 断言 _backfill_fqn 能匹配到
    该行。POSIX 上 as_posix()==str() 故本测通过靠恒等, 但它锁定的是"消费者(_backfill)
    与生产者(投影写入)两侧锚同为正斜杠"这条契约 —— Windows CI 上若 line 212 回退成
    str(cand.abs_path)(反斜杠), 消费侧 '\\' 与 DB 里生产侧 '/' 失配, 此断言即红。
    评分标准: 种 as_posix 锚 + 仓外 _Candidate -> 命中该行返回 FQN, 即契约成立。"""
    from contextos.code_intel.source_search import _Candidate, _backfill_fqn
    ext_root = tmp_path / "external_center"
    java = ext_root / "svc" / "X.java"
    _write(java, "package p;\nclass X {\n  void h() {\n  }\n}\n")
    anchor = java.resolve().as_posix()               # 生产者写投影用的正斜杠绝对锚
    engine = _projection_engine_with_method(
        anchor, "p.X", "p.X.h()", start=3, end=4)
    cand = _Candidate(abs_path=java.resolve(), root=ext_root.resolve(),
                      rel_to_root="svc/X.java", repo_relative=None)  # 仓外 -> 走 abs 分支
    class_fqn, method_fqn = _backfill_fqn(engine, cand, line=3)
    assert method_fqn == "p.X.h()"                    # 消费侧锚(as_posix)== 生产侧锚 -> 命中
    assert class_fqn == "p.X"
