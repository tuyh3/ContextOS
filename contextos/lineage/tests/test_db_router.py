"""Task 12: DbRouter(owner->TNS 路由 + fan-out 兜底 + lazy 缓存)测试。

设计思路:
- resolve_owner_for_table: 从 table_metadata 唯一 owner 才返回(多 owner/未知 -> None)
- querier_for_owner: 经 owner_routing 路由 + lazy 缓存(连接失败降级 None)
- fan_out: 连所有 allowed_instances,过滤 None(连失败)
- close: 收尾(调用 __exit__)

评分标准:
1. 单 owner 路由精准——querier_for_owner("UPC") -> 只连 UPC 对应 TNS
2. 歧义(多 owner 同名表)-> None
3. lazy 缓存——同 owner 只连一次
4. 未知 owner -> None 不崩
5. fan_out 返回所有连接成功的客户端
6. connect 失败 -> None 不崩,fan_out 返回空列表
"""
from contextos.lineage import store, db_router
from contextos.storage.db import make_engine


class _FakeProfile:
    class oracle:
        allowed_instances = ["TEST_DB1", "TEST_DB3"]


def _engine_with_routing():
    e = make_engine("sqlite://"); store.create_all(e)
    store.set_owner_routing(e, {"UPC": "TEST_DB1", "SEC": "TEST_DB3"})
    store.write_table_metadata(e, [
        dict(owner="UPC", template_name="T_UPC", db_name="CCRM3", comment="", dataset_type="TABLE"),
        dict(owner="SEC", template_name="T_SEC", db_name="VCDB", comment="", dataset_type="TABLE")])
    return e


def test_resolve_owner_for_table_single():
    e = _engine_with_routing()
    r = db_router.DbRouter(_FakeProfile(), e, connect=lambda tns: f"client:{tns}")
    assert r.resolve_owner_for_table("T_UPC") == "UPC"


def test_resolve_owner_ambiguous_returns_none():
    e = _engine_with_routing()
    store.write_table_metadata(e, [dict(owner="OTHER", template_name="T_UPC",
                                        db_name="X", comment="", dataset_type="TABLE")])
    r = db_router.DbRouter(_FakeProfile(), e, connect=lambda tns: tns)
    assert r.resolve_owner_for_table("T_UPC") is None     # 多 owner 同名 -> 歧义


def test_querier_for_owner_routes_and_caches():
    e = _engine_with_routing()
    calls = []
    r = db_router.DbRouter(_FakeProfile(), e, connect=lambda tns: calls.append(tns) or f"c:{tns}")
    assert r.querier_for_owner("UPC") == "c:TEST_DB1"
    assert r.querier_for_owner("UPC") == "c:TEST_DB1"
    assert calls == ["TEST_DB1"]                   # lazy + 缓存, 只连一次


def test_querier_for_unknown_owner_none():
    e = _engine_with_routing()
    r = db_router.DbRouter(_FakeProfile(), e, connect=lambda tns: tns)
    assert r.querier_for_owner("GHOST") is None


def test_fan_out_connects_all_instances():
    e = _engine_with_routing()
    r = db_router.DbRouter(_FakeProfile(), e, connect=lambda tns: f"c:{tns}")
    assert r.fan_out() == ["c:TEST_DB1", "c:TEST_DB3"]


def test_connect_failure_yields_none_not_crash():
    e = _engine_with_routing()

    def bad(tns):
        raise RuntimeError("ORA-12541")

    r = db_router.DbRouter(_FakeProfile(), e, connect=bad)
    assert r.querier_for_owner("UPC") is None
    assert r.fan_out() == []


def test_failed_connection_not_pinned_reconnects_after_ttl():
    """连失败不永久钉死: 负缓存 TTL 内不重连(省 connect timeout), 过期后重连 ->
    VPN/库恢复时下次 health_check/查询自动复活。本次 fix 核心: 原来失败缓存 None 永不重连,
    MCP server 进程级 router 一旦在离线期连过一次, 即便库恢复也得重启才能再连。"""
    e = _engine_with_routing()
    now = [0.0]
    state = {"down": True}
    attempts: list[str] = []

    def flaky(tns):
        attempts.append(tns)
        if state["down"]:
            raise RuntimeError("ORA-12541 no listener")
        return f"live:{tns}"

    r = db_router.DbRouter(_FakeProfile(), e, connect=flaky,
                           clock=lambda: now[0], failure_ttl_seconds=30.0)

    # t=0 库宕: 连一次失败 -> None, 负缓存
    assert r.querier_for_owner("UPC") is None
    assert attempts == ["TEST_DB1"]

    # t=10 仍在 TTL 窗口内: 负缓存命中, 不再重试(省 connect timeout), 即便库已恢复也先不连
    now[0] = 10.0
    state["down"] = False
    assert r.querier_for_owner("UPC") is None
    assert attempts == ["TEST_DB1"]                 # 没有第二次 connect

    # t=40 TTL 过期: 重连 -> 成功 -> 返回 live client(此断言在旧"永久 None"逻辑下会失败)
    now[0] = 40.0
    assert r.querier_for_owner("UPC") == "live:TEST_DB1"
    assert attempts == ["TEST_DB1", "TEST_DB1"]


def test_successful_connection_cached_indefinitely():
    """连成功长期缓存, 不随 TTL 过期(只有失败才负缓存重试); 防误给成功连接也加 TTL。"""
    e = _engine_with_routing()
    now = [0.0]
    attempts: list[str] = []
    r = db_router.DbRouter(
        _FakeProfile(), e,
        connect=lambda tns: attempts.append(tns) or f"live:{tns}",
        clock=lambda: now[0], failure_ttl_seconds=30.0)

    assert r.querier_for_owner("UPC") == "live:TEST_DB1"
    now[0] = 10_000.0                                      # 远超任何 TTL
    assert r.querier_for_owner("UPC") == "live:TEST_DB1"
    assert attempts == ["TEST_DB1"]                 # 只连一次, 成功连接不过期


def test_close_calls_exit_and_clears_cache():
    """close() 调用已缓存连接的 __exit__, 然后清空 _cache。"""
    e = _engine_with_routing()
    exit_calls: list[str] = []

    class _FakeClient:
        def __init__(self, tns: str) -> None:
            self._tns = tns

        def __exit__(self, *_: object) -> None:
            exit_calls.append(self._tns)

    r = db_router.DbRouter(_FakeProfile(), e, connect=lambda tns: _FakeClient(tns))
    # 触发连接建立, 使 _cache 有 live 对象
    r.querier_for_owner("UPC")
    r.querier_for_owner("SEC")
    assert len(r._cache) == 2

    r.close()
    assert set(exit_calls) == {"TEST_DB1", "TEST_DB3"}
    assert r._cache == {}  # 缓存清空


def test_close_double_close_does_not_crash():
    """close() 后再次 close() 不崩(double-close 安全)。"""
    e = _engine_with_routing()
    r = db_router.DbRouter(_FakeProfile(), e, connect=lambda tns: f"c:{tns}")
    r.querier_for_owner("UPC")
    r.close()
    r.close()  # second close on empty cache — must not raise
    assert r._cache == {}


def test_context_manager_protocol_calls_close():
    """with DbRouter(...) as r 正常用法: __exit__ 调 close(), 缓存清空。"""
    e = _engine_with_routing()
    exit_calls: list[str] = []

    class _FakeClient:
        def __init__(self, tns: str) -> None:
            self._tns = tns

        def __exit__(self, *_: object) -> None:
            exit_calls.append(self._tns)

    with db_router.DbRouter(_FakeProfile(), e, connect=lambda tns: _FakeClient(tns)) as r:
        r.querier_for_owner("UPC")
        assert len(r._cache) == 1

    # 离开 with 块后 close() 已被调用
    assert "TEST_DB1" in exit_calls
    assert r._cache == {}


# --------------------------------------------------------------------------- fresh env(319c105 家族补漏)


def _fresh_engine():
    """fresh 库(只跑过 init --only code 的形状): engine 可连但血缘表族整个不存在。"""
    return make_engine("sqlite://")


def test_resolve_owner_for_table_fresh_db_returns_none():
    """table_metadata 未建 -> 视同"无元数据"返 None, 不裸抛 OperationalError。
    此前 319c105 只守了 tools 层自己的表, router 这层漏了 —— 而 MCP 真实调用路径
    (app_ctx.oracle_router)必带真 DbRouter, rc.2 自跑时 4 工具在此炸出。"""
    r = db_router.DbRouter(_FakeProfile(), _fresh_engine(), connect=lambda tns: f"c:{tns}")
    assert r.resolve_owner_for_table("ANY_TABLE") is None


def test_querier_for_owner_fresh_db_degrades_none():
    """owner_routing 未建 -> 按"无路由"降级, querier_for_owner 返 None 不裸抛。"""
    r = db_router.DbRouter(_FakeProfile(), _fresh_engine(), connect=lambda tns: f"c:{tns}")
    assert r.querier_for_owner("UPC") is None
