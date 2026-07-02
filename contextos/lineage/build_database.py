"""05 数据库维度子编排(Block 2): 消化 Block 1b 孤儿成一个可调。

discover_owners -> refresh_*_multi(多库元数据 + owner_routing)-> build_index_from_store(dblink)
-> build_lineage(dblink_index)-> build_object_lineage(dblink_index)。

fail-safe(spec §4.2/§4.3): 先对所有实例 discover_owners; 任一失败 / total owners==0 ->
跳过元数据 refresh 保留旧快照(degraded), 仍跑静态/对象血缘。连接经注入 connect(测试可 fake),
默认走 connect_from_profile(红线#4 白名单)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from contextos.lineage.dblink_resolve import build_index_from_store
from contextos.lineage.object_lineage import build_object_lineage
from contextos.lineage.oracle_metadata import (
    discover_owners, refresh_metadata_multi, refresh_object_metadata_multi,
)
from contextos.lineage.pipeline import build_lineage

log = logging.getLogger(__name__)


@dataclass
class InstanceSpec:
    tns: str
    db_name: str
    owners: list[str] = field(default_factory=list)


def _default_connect(profile: Any) -> Callable[[str], Any]:
    def _conn(tns: str):
        from contextos.db_provider.sqlcl_mcp import connect_from_profile
        return connect_from_profile(profile, tns=tns).__enter__()
    return _conn


def build_database_dimension(profile: Any, engine: Any, *, now: str, repo_root: Path,
                             skip_oracle: bool = False,
                             connect: Callable[[str], Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"oracle_status": "offline", "owners": {}, "detail": ""}
    # None = 离线/降级老行为(NameResolver 剥 @dblink 不富化, spec §4.2); 连上才填解析字典
    dblink_index: dict[str, str] | None = None

    if not skip_oracle:
        connect = connect or _default_connect(profile)
        cache: dict[str, Any] = {}

        def factory(tns: str) -> Any:
            if tns not in cache:
                cache[tns] = connect(tns)
            return cache[tns]

        try:
            specs: list[InstanceSpec] = []
            total = 0
            for tns in profile.oracle.allowed_instances:
                q = factory(tns)
                owners = discover_owners(q, profile.tables.exclude_schemas)   # 失败抛
                db_name = profile.oracle.instance_alias.get(tns, tns)
                specs.append(InstanceSpec(tns=tns, db_name=db_name, owners=owners))
                result["owners"][tns] = owners
                total += len(owners)
                log.info("  %s: 发现 %d owner", tns, len(owners))
            if total > 0:
                # 方案 B: 表名排除模式从 profile 取, 透传到元数据抓取(服务端 NOT REGEXP_LIKE
                # 排历史/分区/备份/临时表, 削减某大型客户代码库海量字典抓取量, 实测 ~3x)。
                excl = list(profile.tables.exclude_table_patterns or [])
                if excl:
                    # 不静默削减: 明确日志说明元数据抓取按这些表名模式排除了历史/分区表。
                    log.info("  表名排除生效: %d 模式(历史/分区/备份表不抓元数据)-> %s",
                             len(excl), excl)
                m = refresh_metadata_multi(engine, specs, querier_factory=factory, now=now,
                                           exclude_table_patterns=excl)
                # option A: 默认 lineage scope -> 只抓表级血缘需要的 dependencies + dblinks, 跳过
                # columns/indexes/constraints(per-table 重查, 某大型客户满库抓列 ~40min, 唯一消费方是
                # config 维度)。profile.tables.fetch_full_object_metadata=True 才抓全 8 类(将来 config
                # 按 LP 模板归并抓列时 opt-in)。
                obj_scope = "full" if profile.tables.fetch_full_object_metadata else "lineage"
                if obj_scope == "lineage":
                    log.info("  对象元数据 scope=lineage: 只抓 dependencies+dblinks(跳过 columns 等; "
                             "config 维度需要列时置 profile.tables.fetch_full_object_metadata=true)")
                om = refresh_object_metadata_multi(engine, specs, querier_factory=factory, now=now,
                                                   exclude_table_patterns=excl, scope=obj_scope)
                dblink_index, unresolved = build_index_from_store(engine, profile.oracle)
                dblinks = {"resolved": len(dblink_index), "unresolved": len(unresolved)}
                # HIGH-1: oracle_status 必须反映 refresh 是否真成功, 不能只凭'连上了'(total>0)。
                # refresh_*_multi 是 fail-safe: 拉失败返回 refreshed=False 并保留旧快照; 此时
                # 谎报 connected -> _step_database 报 ok -> verdict=ready -> exit 0(I1 同类谎报)。
                if m.get("refreshed") and om.get("refreshed"):
                    result.update(oracle_status="connected", metadata=m, object_metadata=om,
                                  dblinks=dblinks)
                    log.info("  元数据 %s 表 / dblink %d 解析(%d 未解析)",
                             m.get("tables"), len(dblink_index), len(unresolved))
                else:
                    reasons = []
                    if not m.get("refreshed"):
                        reasons.append(f"表元数据: {m.get('reason', 'not refreshed')}")
                    if not om.get("refreshed"):
                        reasons.append(f"对象元数据: {om.get('reason', 'not refreshed')}")
                    detail = "Oracle 连上但元数据未刷新(保留旧快照): " + "; ".join(reasons)
                    result.update(oracle_status="degraded", metadata=m, object_metadata=om,
                                  detail=detail, dblinks=dblinks)
                    log.warning("  %s", detail)
            else:
                result["oracle_status"] = "degraded"
                result["detail"] = "no owners discovered (保留旧快照)"
                log.warning("  无 owner 发现 -> 跳过元数据 refresh, 保留旧快照")
        except Exception as exc:  # noqa: BLE001  discover/连接失败 -> 保留旧快照(refresh 未执行)
            result["oracle_status"] = "degraded"
            result["detail"] = f"{type(exc).__name__}: {exc}"
            log.warning("  Oracle 元数据降级(%s) -> 保留旧快照, 仍跑静态血缘", exc)
        finally:
            for c in cache.values():
                if hasattr(c, "__exit__"):
                    try:
                        c.__exit__(None, None, None)
                    except Exception:  # noqa: BLE001
                        pass

    lin = build_lineage(repo_root, profile.code, profile.tables, engine, now,
                        dblink_index=dblink_index)
    obj = build_object_lineage(engine, profile.tables, now=now, dblink_index=dblink_index)
    result["lineage"] = lin
    result["object_lineage"] = obj
    log.info("  静态血缘 %d 边 / 对象血缘 %d 边", lin.get("edges"), obj.get("edges"))
    return result
