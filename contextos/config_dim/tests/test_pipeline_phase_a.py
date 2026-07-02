"""Phase A build 端到端(离线 fixture)。

设计思路(memory feedback_contextos_test_documentation):
- 造一个最小仓: 1 个 .properties(含 1 敏感 jdbc.password + 1 普通 app.url)+ 1 个
  Java 类(@Value("${app.url}"))。验证 build_file_config 把文件配置抽取/落库的整链:
  parser -> config_sources/snapshots/entities/items + extract.py(@Value AST FQN)->
  bind_resolver -> config_bindings。
- 评分标准(assert):
  1. stats sources>=1 且 items>=2(properties 2 项落库)。
  2. 敏感链(HIGH 1 sanitizer chokepoint): jdbc.password 落库 value_raw 掩码(****开头)+
     is_sensitive=1 + value_fingerprint 非空; app.url 明文保留 + is_sensitive=0。
  3. 绑定链(MEDIUM 2 AST FQN 主锚): app.url -> com.x.AppCfg(@Value 抽到的 AST 全限定名,
     离线无 searcher 直接采信 AST FQN)。
- 自动脚本测试逻辑: sqlite in-memory engine + metadata.create_all,build 后用 select 读回
  config_items / config_bindings 断言。全离线,不碰 Oracle/RAG。

蓝本偏离(deviations):plan 蓝本 test 用 `Profile()`,但真实 Profile 有多个无默认值必填字段
(llm/embedding/storage/oracle/...),`Profile()` 无参构造会 ValidationError。build_file_config
只读 `profile.config`(下游 C5 契约一致: 传整 profile, 读 profile.config),故本测试用最小
stand-in 对象暴露真实 `ConfigConfig()`(带真实默认词表/扩展名/黑名单),既不改函数签名也不
硬凑。参照 Plan 05 build_lineage 直接吃 sub-config 的同源做法。
"""
import pytest
from pathlib import Path

from sqlalchemy import create_engine, func, insert, select

from contextos.config_dim.pipeline import build_file_config, _java_files_with_config_markers
from contextos.config_dim.schema import (
    config_bindings, config_confirmation, config_items, config_sources, metadata,
)
from contextos.profile.schema import ConfigConfig


class _ProfileStub:
    """build_file_config 只触碰 .config;用真实 ConfigConfig 保真默认行为。"""

    def __init__(self) -> None:
        self.config = ConfigConfig()


def _fixture(tmp: Path) -> None:
    (tmp / "conf").mkdir()
    (tmp / "conf" / "application.properties").write_text(
        "app.url=http://h/svc\njdbc.password=supersecret3f7a\n", encoding="utf-8")
    (tmp / "src").mkdir()
    (tmp / "src" / "AppCfg.java").write_text(
        'package com.x;\nclass AppCfg {\n'
        ' @org.springframework.beans.factory.annotation.Value("${app.url}")\n'
        ' String u;\n}\n',
        encoding="utf-8")


def test_phase_a_build_offline(tmp_path):
    _fixture(tmp_path)
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    stats = build_file_config(
        repo_root=tmp_path, profile=_ProfileStub(), engine=eng, cache_dir=tmp_path)
    assert stats["sources"] >= 1 and stats["items"] >= 2
    with eng.connect() as c:
        items = {r.config_key: r for r in c.execute(select(config_items))}
        # 敏感: jdbc.password 掩码 + is_sensitive=1 + fingerprint 非空
        pw = items["password"] if "password" in items else items["jdbc.password"]
        assert pw.value_raw.startswith("****") and pw.is_sensitive == 1 and pw.value_fingerprint
        assert items["app.url"].value_raw == "http://h/svc" and items["app.url"].is_sensitive == 0
        # 绑定: app.url -> com.x.AppCfg(@Value 抽到, AST FQN)
        binds = list(c.execute(select(config_bindings)))
        assert any(b.bind_target == "com.x.AppCfg" for b in binds)


def test_phase_a_duplicate_key_in_one_file_deduped(tmp_path):
    """同一配置文件里同 key 出现两次(properties 语义=后者覆盖前者)-> build_file_config 不崩, 去重保留最后一个。

    设计思路(memory feedback_contextos_test_documentation):
    - 复现 config_dim 幂等 bug(2026-06-07 init 真扫全仓暴露): 原 build_file_config 对 pc.items
      原样逐条 insert, 同文件重复 key_path 让第二条撞 config_items PK(item_id) + uq_item
      (source_id,key_path,snapshot_id) -> IntegrityError, 整个 config 维 build 崩。
    - 最小仓: 1 个 .properties, 同 key app.retries 两行(先 3 后 5; 中性合成名)。
    - 评分: build 不抛; config_items 里该 key 只 1 条(已去重); value_raw=='5'(后者覆盖)。
    """
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "application.properties").write_text(
        "app.retries=3\napp.retries=5\n", encoding="utf-8")
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    stats = build_file_config(
        repo_root=tmp_path, profile=_ProfileStub(), engine=eng, cache_dir=tmp_path)
    assert stats["items"] == 1
    with eng.connect() as c:
        rows = [r for r in c.execute(select(config_items)) if r.key_path == "app.retries"]
    assert len(rows) == 1
    assert rows[0].value_raw == "5"


def test_phase_a_build_is_idempotent_on_rerun(tmp_path):
    """build_file_config 重跑(库里已有上次 build 数据)不崩、不翻倍。

    设计思路: 复现 init 重跑暴露的真 bug —— 原 build 无'重建前清空', 第二次跑同文件
    generate_id('src', rel) 产同 source_id -> config_sources PK 撞 -> IntegrityError。
    评分: 连跑两次都不抛; 第二次后 config_sources / config_items 计数等于单次(未翻倍)。
    """
    _fixture(tmp_path)
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    s1 = build_file_config(repo_root=tmp_path, profile=_ProfileStub(), engine=eng, cache_dir=tmp_path)
    s2 = build_file_config(repo_root=tmp_path, profile=_ProfileStub(), engine=eng, cache_dir=tmp_path)
    assert s1["sources"] == s2["sources"] and s1["items"] == s2["items"]
    with eng.connect() as c:
        n_src = c.execute(select(func.count()).select_from(config_sources)).scalar()
        n_item = c.execute(select(func.count()).select_from(config_items)).scalar()
    assert n_src == s1["sources"] and n_item == s1["items"]     # 未翻倍


def test_phase_a_rerun_preserves_human_confirmation(tmp_path):
    """重建前清空只清自动抽取数据, 不碰人工权威表 config_confirmation(human_confirmed loop 权威)。"""
    _fixture(tmp_path)
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    build_file_config(repo_root=tmp_path, profile=_ProfileStub(), engine=eng, cache_dir=tmp_path)
    # 人工确认一条(权威覆盖), 然后再 build 一次, 确认仍在
    with eng.begin() as c:
        c.execute(insert(config_confirmation).values(
            customer_id="default", ref_type="config_table", ref_key="OWNER_X.T_CFG",
            decision="confirm", reviewer="alice"))
    build_file_config(repo_root=tmp_path, profile=_ProfileStub(), engine=eng, cache_dir=tmp_path)
    with eng.connect() as c:
        confs = list(c.execute(select(config_confirmation)))
    assert len(confs) == 1 and confs[0].ref_key == "OWNER_X.T_CFG"


# --- C 修复: java 配置标记预筛(ripgrep), 避免全仓逐个 Python AST 解析 ---

def test_java_prefilter_keeps_only_marker_files(tmp_path):
    """ripgrep 预筛只返回含 extract_config_refs 信号的 java(注解 + 配置方法名), 跳过纯逻辑类。

    设计思路(memory feedback_contextos_test_documentation): C 性能修复的健全性守门 ——
    预筛必须命中 @Value 注解(含全限定写法)和 getString 这类 _CONFIG_METHODS, 才不会漏掉
    extract_config_refs 本会抽到的引用; 同时跳过既无注解也无配置方法的纯工具类(省解析)。
    评分: 含注解的 + 含配置方法的两个文件被保留, 纯 add() 工具类被跳过。全离线(真跑 rg)。
    """
    (tmp_path / "WithAnno.java").write_text(
        "package p;\nclass WithAnno {\n"
        ' @org.springframework.beans.factory.annotation.Value("${a.b}") String s;\n}\n',
        encoding="utf-8")
    (tmp_path / "WithMethod.java").write_text(
        'package p;\nclass WithMethod { void f(java.util.Properties pr){ pr.getString("a.b"); } }\n',
        encoding="utf-8")
    (tmp_path / "Plain.java").write_text(
        "package p;\nclass Plain { int add(int a, int b){ return a + b; } }\n", encoding="utf-8")
    got = {p.name for p in _java_files_with_config_markers(tmp_path, [])}
    assert got == {"WithAnno.java", "WithMethod.java"}


def test_java_prefilter_framework_annotation_opt_in(tmp_path):
    """自研框架注解默认不命中, 配进 framework_annotations 才命中 —— 预筛与 extract 同源, 不漏。"""
    (tmp_path / "Fw.java").write_text(
        'package p;\nclass Fw { @MyCfg("a.b") String s; }\n', encoding="utf-8")
    assert {p.name for p in _java_files_with_config_markers(tmp_path, [])} == set()
    assert {p.name for p in _java_files_with_config_markers(tmp_path, ["MyCfg"])} == {"Fw.java"}


def test_java_prefilter_falls_back_to_full_scan_without_rg(tmp_path, monkeypatch):
    """rg 不可用 -> 回退全扫(返回所有 java), 宁可慢不可漏(健全性兜底, 不因缺工具静默漏文件)。"""
    (tmp_path / "Plain.java").write_text("class Plain {}\n", encoding="utf-8")
    monkeypatch.setattr("contextos.config_dim.pipeline.shutil.which", lambda _x: None)
    assert {p.name for p in _java_files_with_config_markers(tmp_path, [])} == {"Plain.java"}


def test_config_marker_terms_cover_all_extract_signals():
    """预筛词表 = extract 认的全部信号(sound-by-construction): 改 _CONFIG_METHODS 等这里自动跟上。"""
    from contextos.config_dim.extract import (
        _CONFIG_METHODS, _PREFIX_ANNOS, _VALUE_ANNOS, config_marker_terms,
    )
    annos, methods = config_marker_terms(["MyCfg"])
    assert _VALUE_ANNOS <= annos and _PREFIX_ANNOS <= annos and "MyCfg" in annos
    assert methods == _CONFIG_METHODS


def test_phase_a_inserts_are_batched(tmp_path):
    """build_file_config 应批量 insert(executemany)而非逐行。

    设计思路(memory feedback_contextos_test_documentation): profile 实测 config 维主瓶颈 =
    逐行 INSERT 43 万+ 次, 每次各付一次 SQLAlchemy 语句构建开销。守护手段: 用 SQLAlchemy
    before_cursor_execute 事件数 INSERT 执行次数 —— 批量后应是 O(表数) 而非 O(行数)。
    评分: 单文件 10 个配置项, 逐行实现会执行 ~22 次 INSERT(1 source+1 snap+10 ent+10 item);
    批量(executemany)后每张表至多 1 次, 总 INSERT 执行 <= 6。全离线 in-memory。
    """
    from sqlalchemy import event
    (tmp_path / "conf").mkdir()
    txt = "\n".join(f"app.k{i}=v{i}" for i in range(10)) + "\n"
    (tmp_path / "conf" / "a.properties").write_text(txt, encoding="utf-8")
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    n_insert = {"v": 0}

    @event.listens_for(eng, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.lstrip().upper().startswith("INSERT"):
            n_insert["v"] += 1

    stats = build_file_config(repo_root=tmp_path, profile=_ProfileStub(), engine=eng, cache_dir=tmp_path)
    assert stats["items"] == 10
    assert n_insert["v"] <= 6, f"INSERT 执行 {n_insert['v']} 次, 未批量(逐行?)"


def test_phase_a_dedups_duplicate_bindings(tmp_path):
    """同一类对同一配置 key 多次引用 -> 多条相同 binding_id 的 binding, 批量 insert 前必须去重。

    设计思路(memory feedback_contextos_test_documentation): binding_id = id(entity, bind_type,
    bind_target), 同(类, key)被多个 ref 命中就重复。perf 修复(批量 insert + bind_resolver 索引)
    后 config 终于跑到 binding insert 才暴露这个潜伏的 PK 撞车(与 config_items 重复 key 同一类病)。
    评分: Foo 里两次 getProperty("app.url") -> 两条相同 binding -> 去重后只落 1 条, build 不崩。全离线。
    """
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "a.properties").write_text("app.url=x\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Foo.java").write_text(
        "package p;\nclass Foo {\n"
        ' void a(java.util.Properties pr){ pr.getProperty("app.url"); }\n'
        ' void b(java.util.Properties pr){ pr.getProperty("app.url"); }\n}\n',
        encoding="utf-8")
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    build_file_config(repo_root=tmp_path, profile=_ProfileStub(), engine=eng, cache_dir=tmp_path)
    with eng.connect() as c:
        binds = list(c.execute(select(config_bindings)))
    assert len(binds) == 1


@pytest.mark.cmd_boundary
def test_java_prefilter_non_ascii_and_space_path(tmp_path):
    """路径流 NUL 切: 中文 / 空格 java 文件名命中预筛后路径正确(os.fsdecode), 不碎不漏。"""
    import shutil as _sh
    if _sh.which("rg") is None:
        pytest.skip("rg not installed")
    (tmp_path / "中文 目录").mkdir()
    f = tmp_path / "中文 目录" / "Fw Cfg.java"
    f.write_text(
        ' @org.springframework.beans.factory.annotation.Value("${a.b}") String s;\n', encoding="utf-8")
    got = _java_files_with_config_markers(tmp_path, [])
    assert any(p.name == "Fw Cfg.java" for p in got)
    # 返回的 Path 能 read(路径解码正确, 非乱码)
    assert any(p.read_text(encoding="utf-8").strip().endswith("String s;") for p in got)
