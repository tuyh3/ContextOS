"""build_database_dimension 单测(Block 2 + 多方言 L1c)。

设计思路:
  - 用注入 connect(fake querier)驱动 build_database_dimension 全路径, 不连真 Oracle:
    connected / discover 失败 / 零 owner / refresh 中途断 / 写入失败 / lineage scope /
    full scope / skip_db / fresh 库建表, 逐条锁 fail-safe 与状态诚实契约。
  - L1c(spec 附录 A.4)内部契约键中性化: 结果主键 = db_status; oracle_status 保留为
    过渡期兼容别名(值恒等于 db_status), 由 test_build_database_result_keeps_oracle_status_alias
    锁死, 别名移除时该测试一并删。

评分标准:
  - 各降级路径 db_status 必须如实(不谎报 connected), 旧快照原子保留;
  - 别名测试: 每条路径 out["oracle_status"] == out["db_status"] 且两键同时存在。

自动脚本测试逻辑:
  - sqlite in-memory engine + store.create_all; fake querier 按 SQL 关键字回声元数据行;
    fixture 全中性合成值(memory feedback_offline_test_neutral_fixtures)。
"""
from contextos.lineage import build_database, store
from contextos.profile.schema import (
    CodeConfig, DaoSqlPattern, EmbeddingConfig, IngestionConfig,
    JdtlsRuntimeConfig, LLMConfig, OracleConfig, Profile, ProjectConfig,
    QueryExpansionConfig, RerankerConfig, StorageConfig, TablesConfig,
)
from contextos.storage.db import make_engine


def _profile(allowed: list[str], *, full_obj: bool = False) -> Profile:
    # 只给 build_database_dimension 用到的字段; 其余用最小合法值
    return Profile(
        llm=LLMConfig(provider="x", api_key_env="X"),
        embedding=EmbeddingConfig(model="m"),
        reranker=RerankerConfig(model="m"),
        query_expansion=QueryExpansionConfig(translation_provider="p", fallback_provider="p"),
        storage=StorageConfig(data_dir="/tmp/cx"),
        ingestion=IngestionConfig(),
        jdtls_runtime=JdtlsRuntimeConfig(jdtls_path="/j", lombok_path="/l", java_home="/h"),
        oracle=OracleConfig(tns_admin="/tns", allowed_instances=allowed),
        code=CodeConfig(dao_sql_patterns=[DaoSqlPattern(path_contains=["/impl/"])]),
        tables=TablesConfig(fetch_full_object_metadata=full_obj),
        projects=[ProjectConfig(name="p", path="/tmp", language="java")],
    )


class _Q:
    """按 owner 回声表 + ALL_OBJECTS owner 列表。"""
    def __init__(self, tns, owners):
        self.tns = tns; self._owners = owners
    def query(self, sql, params=None):
        if "ALL_OBJECTS" in sql:
            return [{"OWNER": o} for o in self._owners]
        # 方案 B 批量: 从 OWNER IN (:o0,...) 的 o-bind 派生 owner, 每 owner 返一行。
        owners = [v for k, v in (params or {}).items() if k[:1] == "o" and k[1:].isdigit()]
        if "ALL_TAB_COMMENTS" in sql:
            return [{"OWNER": o, "TABLE_NAME": f"T_{o}", "TABLE_TYPE": "TABLE", "COMMENTS": ""}
                    for o in owners]
        if "ALL_TAB_COLUMNS" in sql:
            return [{"OWNER": o, "TABLE_NAME": f"T_{o}", "COLUMN_NAME": "C",
                     "DATA_TYPE": "X", "NULLABLE": "Y", "COLUMN_ID": 1, "COMMENTS": ""}
                    for o in owners]
        return []  # synonyms / FK / other metadata queries: empty is a valid non-error response


class _EmptyOwners:
    """ALL_OBJECTS succeeds but returns zero owners (no exception) -> total==0 degraded path."""
    def query(self, sql, params=None):
        return []  # ALL_OBJECTS -> no owners; never raises


def _repo(tmp_path):
    d = tmp_path / "impl"; d.mkdir()
    (d / "Foo.sql").write_text("SELECT ID FROM T_UPC", encoding="utf-8")
    return tmp_path


def test_build_database_multidb_connected(tmp_path):
    e = make_engine("sqlite://"); store.create_all(e)
    prof = _profile(["A", "B"])
    owners = {"A": ["UPC"], "B": ["SEC"]}
    out = build_database.build_database_dimension(
        prof, e, now="2026-06-07T00:00:00", repo_root=_repo(tmp_path),
        connect=lambda tns: _Q(tns, owners[tns]))
    assert out["db_status"] == "connected"
    assert {r["db_name"] for r in store.all_table_metadata(e)} == {"A", "B"}  # db_name 默认=tns(无 alias)
    assert store.all_owner_routing(e) == {"UPC": "A", "SEC": "B"}
    assert out["lineage"]["edges"] >= 0 and "object_lineage" in out


def test_build_database_discover_failure_keeps_snapshot_degraded(tmp_path):
    e = make_engine("sqlite://"); store.create_all(e)
    store.write_table_metadata(e, [dict(owner="OLD", template_name="OLD_T",
                                        db_name="OLDDB", comment="", dataset_type="TABLE")])
    prof = _profile(["A"])

    class _DeadDiscover:
        def query(self, sql, params=None):
            if "ALL_OBJECTS" in sql:
                raise RuntimeError("ORA-00942")
            return []

    out = build_database.build_database_dimension(
        prof, e, now="2026-06-07T00:00:00", repo_root=_repo(tmp_path),
        connect=lambda tns: _DeadDiscover())
    assert out["db_status"] == "degraded"
    assert [r["owner"] for r in store.all_table_metadata(e)] == ["OLD"]   # 旧快照保留, 没 WIPE
    assert "lineage" in out                                              # 静态血缘照建


def test_build_database_zero_owners_degraded(tmp_path):
    # discover 成功但所有实例 owners 全空(无异常)-> total==0 走 degraded, 旧快照保留, 仍跑静态血缘
    e = make_engine("sqlite://"); store.create_all(e)
    store.write_table_metadata(e, [dict(owner="OLD", template_name="OLD_T",
                                        db_name="OLDDB", comment="", dataset_type="TABLE")])
    prof = _profile(["A"])
    out = build_database.build_database_dimension(
        prof, e, now="2026-06-07T00:00:00", repo_root=_repo(tmp_path),
        connect=lambda tns: _EmptyOwners())
    assert out["db_status"] == "degraded"
    assert [r["owner"] for r in store.all_table_metadata(e)] == ["OLD"]   # 没 WIPE
    assert "lineage" in out                                              # 静态血缘照建


def test_build_database_refresh_failure_after_discover_degraded(tmp_path):
    """HIGH-1: discover 成功(total>0)但元数据刷新中途断连/超时 -> db_status 必须 degraded。

    不能因为'连上了'(total>0)就无条件谎报 connected: refresh_*_multi 是 fail-safe,
    拉失败时返回 refreshed=False 并保留旧快照, 此时上报 connected -> _step_database 报 ok
    -> verdict=ready -> exit 0(I1 同类谎报)。discover 查 ALL_OBJECTS、refresh 查
    ALL_TAB_COMMENTS/COLUMNS, 是不同查询; 两次之间断连即触发本路径。"""
    e = make_engine("sqlite://"); store.create_all(e)
    prof = _profile(["A"])

    class _DiscoverOkRefreshFails:
        """ALL_OBJECTS 成功返 owner(discover 过关), 真元数据查询全抛(refresh 中途断)。"""
        def query(self, sql, params=None):
            if "ALL_OBJECTS" in sql:
                return [{"OWNER": "OWNER_A"}]
            raise RuntimeError("ORA-12541 connection lost after discover")

    out = build_database.build_database_dimension(
        prof, e, now="2026-06-07T00:00:00", repo_root=_repo(tmp_path),
        connect=lambda tns: _DiscoverOkRefreshFails())
    assert out["db_status"] == "degraded"     # 不是 "connected"(谎报)
    assert out.get("detail")                        # degraded 必须带可诊断 reason
    assert "lineage" in out                         # 静态血缘仍照建


def test_build_database_write_failure_preserves_snapshot_degraded(tmp_path, monkeypatch):
    """HIGH-2 集成: 元数据写入侧失败(模拟 PG DataError)经 build_database_dimension ->
    db_status degraded(不谎报 connected)+ 旧对象快照原子保留(不半清空)。

    锁死端到端契约: replace_object_metadata 抛 -> 传到 build_database 外层 except -> degraded,
    且回滚保旧。防止未来把 replace 调用挪进 fetch try 吞成 refreshed=False 而丢失诊断。"""
    from contextos.lineage import store as store_mod
    e = make_engine("sqlite://"); store.create_all(e)
    store.write_views(e, [dict(owner="OWNER_X", view_name="OLD_V", comment="", db_name="A")])
    prof = _profile(["A"])

    real = store_mod._insert_rows_conn

    def boom(conn, table, rows):
        if table is store_mod.procedures:
            raise RuntimeError("simulated PG DataError mid-write")
        return real(conn, table, rows)

    monkeypatch.setattr(store_mod, "_insert_rows_conn", boom)

    out = build_database.build_database_dimension(
        prof, e, now="2026-06-07T00:00:00", repo_root=_repo(tmp_path),
        connect=lambda tns: _Q(tns, ["OWNER_X"]))
    assert out["db_status"] == "degraded"                          # 写失败 -> 不谎报 connected
    assert [r["view_name"] for r in store.all_views(e)] == ["OLD_V"]   # 旧对象快照原子保留


def test_build_database_default_skips_columns(tmp_path):
    """option A: 数据库维度默认 lineage scope -> 不抓 columns(那是 config 维度的 ~40min 墙)。
    表元数据(NameResolver 用)+ 静态血缘照常。"""
    e = make_engine("sqlite://"); store.create_all(e)
    prof = _profile(["A"])
    out = build_database.build_database_dimension(
        prof, e, now="2026-06-07T00:00:00", repo_root=_repo(tmp_path),
        connect=lambda tns: _Q(tns, ["UPC"]))
    assert out["db_status"] == "connected"
    assert store.all_columns(e) == []                       # 默认不抓列
    assert [r["template_name"] for r in store.all_table_metadata(e)] == ["T_UPC"]  # 表元数据仍抓
    assert "lineage" in out


def test_build_database_full_scope_opt_in_fetches_columns(tmp_path):
    """profile.tables.fetch_full_object_metadata=True -> full scope, columns 照抓(将来 config 用)。"""
    e = make_engine("sqlite://"); store.create_all(e)
    prof = _profile(["A"], full_obj=True)
    out = build_database.build_database_dimension(
        prof, e, now="2026-06-07T00:00:00", repo_root=_repo(tmp_path),
        connect=lambda tns: _Q(tns, ["UPC"]))
    assert out["db_status"] == "connected"
    assert [r["column_name"] for r in store.all_columns(e)] == ["C"]   # opt-in 后抓到列


def test_build_database_skip_db_static_only(tmp_path):
    e = make_engine("sqlite://"); store.create_all(e)
    prof = _profile(["A"])
    out = build_database.build_database_dimension(
        prof, e, now="2026-06-07T00:00:00", repo_root=_repo(tmp_path), skip_db=True)
    assert out["db_status"] == "offline"
    assert "lineage" in out and store.all_table_metadata(e) == []


def test_build_database_result_keeps_oracle_status_alias(tmp_path):
    """L1c(spec 附录 A.4): 结果 dict 主键改 db_status 后, oracle_status 保留为过渡期
    兼容别名, 值必须恒等于 db_status —— 三条代表路径各验一次(offline / connected /
    degraded), 防止只在单一分支镜像。别名下线时删本测试即可。"""
    for sub in ("r1", "r2", "r3"):
        (tmp_path / sub).mkdir()      # _repo 在其下建 impl/, 需父目录先存在
    # 路径 1: skip_db -> offline
    e1 = make_engine("sqlite://"); store.create_all(e1)
    out1 = build_database.build_database_dimension(
        _profile(["A"]), e1, now="2026-07-10T00:00:00",
        repo_root=_repo(tmp_path / "r1"), skip_db=True)
    assert out1["db_status"] == "offline"
    assert out1["oracle_status"] == out1["db_status"]
    # 路径 2: 正常连上 -> connected
    e2 = make_engine("sqlite://"); store.create_all(e2)
    out2 = build_database.build_database_dimension(
        _profile(["A"]), e2, now="2026-07-10T00:00:00",
        repo_root=_repo(tmp_path / "r2"), connect=lambda tns: _Q(tns, ["UPC"]))
    assert out2["db_status"] == "connected"
    assert out2["oracle_status"] == out2["db_status"]
    # 路径 3: discover 抛 -> degraded
    class _Dead:
        def query(self, sql, params=None):
            raise RuntimeError("ORA-00942")
    e3 = make_engine("sqlite://"); store.create_all(e3)
    out3 = build_database.build_database_dimension(
        _profile(["A"]), e3, now="2026-07-10T00:00:00",
        repo_root=_repo(tmp_path / "r3"), connect=lambda tns: _Dead())
    assert out3["db_status"] == "degraded"
    assert out3["oracle_status"] == out3["db_status"]


def test_build_database_fresh_db_creates_tables_first(tmp_path):
    """fresh-env 家族第三成员回归锚(2026-07-04 rc.3 真机抓到): fresh 库(不 create_all)
    直接跑 database 维, 元数据 refresh 的 DELETE+写必须能落 —— 修前在真连拉完元数据后
    裸炸 no such table: table_metadata。评分标准: 与 connected 用例同输入下, fresh 库
    照样 connected 且元数据/路由真实落库, 零 OperationalError。"""
    e = make_engine("sqlite://")                      # 关键: 不 create_all
    prof = _profile(["A", "B"])
    owners = {"A": ["UPC"], "B": ["SEC"]}
    out = build_database.build_database_dimension(
        prof, e, now="2026-07-04T00:00:00", repo_root=_repo(tmp_path),
        connect=lambda tns: _Q(tns, owners[tns]))
    assert out["db_status"] == "connected"
    assert {r["db_name"] for r in store.all_table_metadata(e)} == {"A", "B"}
    assert store.all_owner_routing(e) == {"UPC": "A", "SEC": "B"}
