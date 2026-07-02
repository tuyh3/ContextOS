"""查询期多库 router(Block 1b, 05 §8.4)。

按表 owner -> 来源库 TNS 路由到对应连接(每库 lazy 建 + 缓存); owner 解不出时
fan-out 所有白名单实例合并。红线 #4: 连接走注入的 connect(默认 connect_from_profile,
白名单 + prod 关键词硬拒)。读快照的 tool 不用本 router(直接查 store)。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from contextos.lineage import store

log = logging.getLogger(__name__)


def _default_connect(profile: Any) -> Callable[[str], Any]:
    def _conn(tns: str) -> Any:
        from contextos.db_provider.sqlcl_mcp import connect_from_profile
        return connect_from_profile(profile, tns=tns).__enter__()
    return _conn


_DEFAULT_FAILURE_TTL_SECONDS = 30.0  # 连失败的负缓存有效期; 过期后下次调用重连(VPN 恢复自愈)


class DbRouter:
    """查询期 owner -> TNS 路由 + fan-out 兜底 + lazy 连接缓存。

    连接失败降级 None/[](不崩)。红线 #4 由 _default_connect 走 connect_from_profile 守。
    connect 参数可注入 fake 供测试。

    缓存语义(两类分开): 成功连接长期缓存(复用, 不过期); 连失败只做**负缓存 TTL**——
    TTL 窗口内不重连(离线时省掉每次查询的 connect timeout), 过期后下次调用自动重连。
    这样 VPN/库恢复后, 下次 health_check / 查询会在一个 TTL 窗口内自动复活, **不必重启
    MCP server**(进程级 router 缓存 self._oracle_router 会一直复用同一个本对象)。
    clock / failure_ttl_seconds 可注入供测试(确定性驱动时钟 + 小 TTL)。
    """

    def __init__(self, profile: Any, engine: Any, *,
                 connect: Callable[[str], Any] | None = None,
                 clock: Callable[[], float] | None = None,
                 failure_ttl_seconds: float = _DEFAULT_FAILURE_TTL_SECONDS) -> None:
        self._profile = profile
        self._engine = engine
        self._connect = connect or _default_connect(profile)
        self._clock = clock or time.monotonic
        self._failure_ttl = failure_ttl_seconds
        self._cache: dict[str, Any] = {}            # tns -> 成功连接(长期缓存; 失败不入此表)
        self._failed_at: dict[str, float] = {}      # tns -> 上次连失败的单调时刻(负缓存, 带 TTL)
        self._owner_map: dict[str, str] | None = None

    def _routing(self) -> dict[str, str]:
        """lazy 加载 owner -> TNS 路由映射(从 store.owner_routing 读)。"""
        if self._owner_map is None:
            self._owner_map = store.all_owner_routing(self._engine)
        return self._owner_map

    def resolve_owner_for_table(self, table: str) -> str | None:
        """table -> owner(table_metadata 里唯一 owner 才返回; 多 owner/未知 -> None)。"""
        tu = (table or "").upper()
        owners = {
            (r["owner"] or "").upper()
            for r in store.all_table_metadata(self._engine)
            if (r["template_name"] or "").upper() == tu and r["owner"]
        }
        return next(iter(owners)) if len(owners) == 1 else None

    def _connect_cached(self, tns: str) -> Any:
        """lazy 建连接: 成功长期缓存; 失败负缓存 TTL 秒(窗口内不重连, 过期重连; 不崩)。

        与旧实现的关键差异: 旧版把失败缓存成 None 且**永不重连**, 离线期连过一次后
        即便库恢复也得重启进程。现在失败只负缓存 TTL, 过期自动重连(VPN 恢复自愈)。
        """
        client = self._cache.get(tns)
        if client is not None:
            return client                              # 成功连接: 长期复用
        failed_at = self._failed_at.get(tns)
        if failed_at is not None and (self._clock() - failed_at) < self._failure_ttl:
            return None                                # 负缓存未过期: 不重连, 直接降级 None
        try:
            client = self._connect(tns)
        except Exception as exc:  # noqa: BLE001  连失败 -> 负缓存(带 TTL), 下次过期重试
            log.warning("DbRouter 连接 %s 失败, 负缓存 %.0fs 后重试: %s",
                        tns, self._failure_ttl, exc)
            self._failed_at[tns] = self._clock()
            return None
        self._cache[tns] = client
        self._failed_at.pop(tns, None)                 # 连成功 -> 清掉负缓存标记
        return client

    def querier_for_owner(self, owner: str) -> Any:
        """按 owner 路由到对应库连接; 未知 owner -> None。"""
        tns = self._routing().get((owner or "").upper())
        return self._connect_cached(tns) if tns else None

    def fan_out(self) -> list[Any]:
        """连所有 allowed_instances, 过滤掉连失败的 None。"""
        out = [self._connect_cached(t) for t in self._profile.oracle.allowed_instances]
        return [c for c in out if c is not None]

    def close(self) -> None:
        """收尾: 调用各连接的 __exit__; 清缓存 + 负缓存。double-close 安全(缓存已空)。"""
        for c in self._cache.values():
            if c is not None and hasattr(c, "__exit__"):
                try:
                    c.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
        self._cache.clear()
        self._failed_at.clear()

    def __enter__(self) -> "DbRouter":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
