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
    assert out["oracle_status"] == "connected"
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
    assert out["oracle_status"] == "degraded"
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
    assert out["oracle_status"] == "degraded"
    assert [r["owner"] for r in store.all_table_metadata(e)] == ["OLD"]   # 没 WIPE
    assert "lineage" in out                                              # 静态血缘照建


def test_build_database_refresh_failure_after_discover_degraded(tmp_path):
    """HIGH-1: discover 成功(total>0)但元数据刷新中途断连/超时 -> oracle_status 必须 degraded。

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
    assert out["oracle_status"] == "degraded"     # 不是 "connected"(谎报)
    assert out.get("detail")                        # degraded 必须带可诊断 reason
    assert "lineage" in out                         # 静态血缘仍照建


def test_build_database_write_failure_preserves_snapshot_degraded(tmp_path, monkeypatch):
    """HIGH-2 集成: 元数据写入侧失败(模拟 PG DataError)经 build_database_dimension ->
    oracle_status degraded(不谎报 connected)+ 旧对象快照原子保留(不半清空)。

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
    assert out["oracle_status"] == "degraded"                          # 写失败 -> 不谎报 connected
    assert [r["view_name"] for r in store.all_views(e)] == ["OLD_V"]   # 旧对象快照原子保留


def test_build_database_default_skips_columns(tmp_path):
    """option A: 数据库维度默认 lineage scope -> 不抓 columns(那是 config 维度的 ~40min 墙)。
    表元数据(NameResolver 用)+ 静态血缘照常。"""
    e = make_engine("sqlite://"); store.create_all(e)
    prof = _profile(["A"])
    out = build_database.build_database_dimension(
        prof, e, now="2026-06-07T00:00:00", repo_root=_repo(tmp_path),
        connect=lambda tns: _Q(tns, ["UPC"]))
    assert out["oracle_status"] == "connected"
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
    assert out["oracle_status"] == "connected"
    assert [r["column_name"] for r in store.all_columns(e)] == ["C"]   # opt-in 后抓到列


def test_build_database_skip_oracle_static_only(tmp_path):
    e = make_engine("sqlite://"); store.create_all(e)
    prof = _profile(["A"])
    out = build_database.build_database_dimension(
        prof, e, now="2026-06-07T00:00:00", repo_root=_repo(tmp_path), skip_oracle=True)
    assert out["oracle_status"] == "offline"
    assert "lineage" in out and store.all_table_metadata(e) == []
