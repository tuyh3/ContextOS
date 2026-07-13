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

from contextos.lineage import store
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


def _resolve_sql_dialect(profile: Any) -> str:
    """从 profile.database.type 解析 sqlglot 方言(traits 单一取值点)。
    解析不出(无 database / 异常)-> "oracle" 兜底(CMPAK 行为不变)。"""
    try:
        from contextos.db_provider.dialects import get_traits
        db = profile.database
        compat = getattr(getattr(db, "opengauss", None), "compat_mode", None)
        return get_traits(db.type, compat_mode=compat).sqlglot_dialect
    except Exception:  # noqa: BLE001
        return "oracle"


def _build_mysql_metadata(profile: Any, engine: Any, *, now: str,
                          result: dict[str, Any],
                          client_factory: Callable[[Any], Any] | None = None) -> None:
    """MySQL 元数据链(spec 附录 D): 逐实例拉 information_schema 累积 -> 一次原子覆盖 +
    owner_routing(owner=库名 -> 实例 alias)。连接/拉取失败 -> degraded 保旧快照, 仍跑静态血缘
    (与 Oracle 同纪律)。client_factory 可注入 fake 供测试; 默认 connect_mysql_from_profile。"""
    from contextos.db_provider.mysql_client import connect_mysql_from_profile
    from contextos.lineage.mysql_metadata import fetch_mysql_table_rows

    instances = profile.database.mysql.instances
    all_rows: list[dict] = []
    routing: dict[str, str] = {}
    total_tab = total_view = 0
    try:
        for inst in instances:
            client = (client_factory(inst) if client_factory is not None
                      else connect_mysql_from_profile(profile, alias=inst.alias))
            with client:
                rows, n_tab, n_view = fetch_mysql_table_rows(
                    client, inst.databases, db_alias=inst.alias)
            all_rows.extend(rows)
            total_tab += n_tab
            total_view += n_view
            for db in inst.databases:
                # owner(库名)大写归一 -> 实例(与 DbRouter.querier_for_owner 的 .upper() 查一致;
                # 方言感知大小写归一在 L3 收口)。
                routing[db.upper()] = inst.alias
                result["owners"].setdefault(inst.alias, []).append(db)
    except Exception as exc:  # noqa: BLE001  连接/拉取失败 -> 保旧快照
        result["db_status"] = "degraded"
        result["detail"] = f"{type(exc).__name__}: {exc}"
        log.warning("  MySQL 元数据降级(%s) -> 保留旧快照, 仍跑静态血缘", exc)
        return
    # 跨实例累积后一次原子覆盖(owner_tns=routing 同时覆盖 owner_routing)
    store.replace_metadata(engine, tables=all_rows, synonyms=[], fks=[],
                           owner_tns=routing, refreshed_at=now)
    result.update(db_status="connected",
                  metadata={"refreshed": True, "tables": total_tab, "views": total_view})
    log.info("  MySQL 元数据: %d 表 / %d 视图 / %d 库路由", total_tab, total_view, len(routing))


def build_database_dimension(profile: Any, engine: Any, *, now: str, repo_root: Path,
                             skip_db: bool = False,
                             connect: Callable[[str], Any] | None = None,
                             mysql_client_factory: Callable[[Any], Any] | None = None
                             ) -> dict[str, Any]:
    # fresh-env 家族第三成员(2026-07-04 rc.3 真跑抓到): 元数据 refresh 的原子覆盖
    # (DELETE+写)跑在建表之前 -- fresh 库(clone 后直接 init --only database)真连
    # Oracle 拉完 81s 元数据后 DELETE FROM table_metadata 裸炸。database 维拥有这族表,
    # 入口先 idempotent 建表(同 init/orchestrator 给 config 维保表的先例); 更晚的
    # pipeline/object_lineage 内 create_all 不受影响。
    store.create_all(engine)
    # L1c(spec 附录 A.4): 内部契约键中性化 oracle_status -> db_status, 主键 = db_status;
    # oracle_status 在函数尾镜像成兼容别名(过渡期), 中间路径只写 db_status。
    result: dict[str, Any] = {"db_status": "offline", "owners": {}, "detail": ""}
    # None = 离线/降级老行为(NameResolver 剥 @dblink 不富化, spec §4.2); 连上才填解析字典
    dblink_index: dict[str, str] | None = None

    db_type = profile.database.type if profile.database is not None else None
    if not skip_db and db_type == "mysql":
        # 按 type 分派: MySQL 走 information_schema 元数据链(附录 D), 不触 Oracle 实例遍历
        _build_mysql_metadata(profile, engine, now=now, result=result,
                              client_factory=mysql_client_factory)
    elif not skip_db:
        connect = connect or _default_connect(profile)
        cache: dict[str, Any] = {}

        def factory(tns: str) -> Any:
            if tns not in cache:
                cache[tns] = connect(tns)
            return cache[tns]

        try:
            specs: list[InstanceSpec] = []
            total = 0
            # 统一取值点 profile.database; 非 oracle 类型 ora=None -> 空循环 ->
            # 走既有 total==0 降级分支(L2 将按 type 分派 MySQL 元数据链)
            ora = profile.database.oracle if profile.database is not None else None
            for tns in (ora.allowed_instances if ora is not None else []):
                q = factory(tns)
                owners = discover_owners(q, profile.tables.exclude_schemas)   # 失败抛
                db_name = ora.instance_alias.get(tns, tns)
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
                dblink_index, unresolved = build_index_from_store(engine, ora)
                dblinks = {"resolved": len(dblink_index), "unresolved": len(unresolved)}
                # HIGH-1: db_status 必须反映 refresh 是否真成功, 不能只凭'连上了'(total>0)。
                # refresh_*_multi 是 fail-safe: 拉失败返回 refreshed=False 并保留旧快照; 此时
                # 谎报 connected -> _step_database 报 ok -> verdict=ready -> exit 0(I1 同类谎报)。
                if m.get("refreshed") and om.get("refreshed"):
                    result.update(db_status="connected", metadata=m, object_metadata=om,
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
                    result.update(db_status="degraded", metadata=m, object_metadata=om,
                                  detail=detail, dblinks=dblinks)
                    log.warning("  %s", detail)
            else:
                result["db_status"] = "degraded"
                result["detail"] = "no owners discovered (保留旧快照)"
                log.warning("  无 owner 发现 -> 跳过元数据 refresh, 保留旧快照")
        except Exception as exc:  # noqa: BLE001  discover/连接失败 -> 保留旧快照(refresh 未执行)
            result["db_status"] = "degraded"
            result["detail"] = f"{type(exc).__name__}: {exc}"
            log.warning("  Oracle 元数据降级(%s) -> 保留旧快照, 仍跑静态血缘", exc)
        finally:
            for c in cache.values():
                if hasattr(c, "__exit__"):
                    try:
                        c.__exit__(None, None, None)
                    except Exception:  # noqa: BLE001
                        pass

    # SQL 方言单一取值点(spec 4.5): 从 profile 解析并存 metadata_meta, 查询期(dataflow)读回。
    sql_dialect = _resolve_sql_dialect(profile)
    store.set_meta(engine, "sql_dialect", sql_dialect)
    # db_type 驱动 mapper 方言目录选择(E.5); 与 sql_dialect 不同(opengauss->postgres 方言)。
    db_type = profile.database.type if profile.database is not None else "oracle"
    lin = build_lineage(repo_root, profile.code, profile.tables, engine, now,
                        dblink_index=dblink_index, dialect=sql_dialect, db_type=db_type)
    obj = build_object_lineage(engine, profile.tables, now=now, dblink_index=dblink_index)
    result["lineage"] = lin
    result["object_lineage"] = obj
    log.info("  静态血缘 %d 边 / 对象血缘 %d 边", lin.get("edges"), obj.get("edges"))
    # 兼容别名(已废弃, spec 附录 A.4, 2026-07-10): oracle_status 保留一个过渡期供旧消费端读,
    # 值恒等于 db_status(尾部统一镜像, 不在各分支散写); 过渡期后连同别名回归测试一起移除。
    result["oracle_status"] = result["db_status"]
    return result
