#!/usr/bin/env python3
"""Plan 06 手测脚本: build 真 repo -> 打印配置维度统计 + 识别的配置表给人核对。

用法:
  # 离线 build(无 Oracle, 默认): 扫真客户项目配置文件 + Java @Value/注解绑定
  uv run python scripts/smoke_config_dim.py --repo /path/to/your/project

  # 离线指定子模块(更快):
  uv run python scripts/smoke_config_dim.py --repo /path/to/your/project/cust

  # 带 Oracle DB 配置表识别(需 config/profile.toml + .env + VPN, 白名单红线 #4):
  uv run python scripts/smoke_config_dim.py --repo .../cust --owner UPC --db CTEST

设计: 自动测试用 Fake/小 fixture(快、确定); 手测用真实客户项目 + 真 Oracle, 人核对
"抽得准不准 / 识别的配置表对不对"(gold-standard 人工, 见 [[feedback_human_in_the_loop_testing]])。
默认离线只跑 Phase A(build_file_config); 带 --owner/--db 才连 Oracle 跑四路识别(Phase B/C)。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine

from contextos.config_dim import schema as S
from contextos.config_dim.pipeline import build_config_dimension, build_file_config
from contextos.storage.db import make_engine


def _load_profile_or_stub():
    """优先用 config/profile.toml(真 Profile, --owner/--db 必需);worktree 没有就退到
    最小 stub 暴露真实 ConfigConfig()/ConfigTablesConfig()(离线只读 .config/.config_tables,
    带真实默认词表/扩展名/规则列, 同 test_pipeline_phase_a.py 做法)。返回 (profile, is_real)。"""
    from contextos.profile.schema import ConfigConfig, ConfigTablesConfig

    toml_path = Path("config/profile.toml")
    if toml_path.exists():
        import tomllib

        from contextos.profile.schema import Profile
        with open(toml_path, "rb") as f:
            return Profile(**tomllib.load(f)), True

    class _ProfileStub:
        def __init__(self) -> None:
            self.config = ConfigConfig()
            self.config_tables = ConfigTablesConfig()

    return _ProfileStub(), False


def _print_phase_a(engine: Engine, stats: dict, top_n: int) -> None:
    print(f"[build] stats={stats}")
    with engine.connect() as c:
        srcs = list(c.execute(select(S.config_sources)))
        ents = list(c.execute(select(S.config_entities)))
        items = list(c.execute(select(S.config_items)))
        binds = list(c.execute(select(S.config_bindings)))

    by_type: dict[str, int] = {}
    for s in srcs:
        by_type[s.source_type] = by_type.get(s.source_type, 0) + 1
    print(f"[build] config_sources={len(srcs)} by_type={by_type}  "
          f"entities={len(ents)}  items={len(items)}  bindings={len(binds)}")

    file_srcs = [s for s in srcs if s.source_type == "file"]
    print(f"\n[sources] 前 {top_n} 个配置文件源(file_type / framework / path):")
    for s in file_srcs[:top_n]:
        print(f"  {s.file_type:12} {s.framework or '-':16} {s.file_path}")

    print(f"\n[entities] 前 {top_n} 个配置实体(entity_type / entity_key):")
    for e in ents[:top_n]:
        print(f"  {e.entity_type:12} {e.entity_key}")

    # 敏感链人工核对: 掩码项必须 ****开头 + fingerprint 非空(HIGH 1 sanitizer chokepoint)
    sens_items = [it for it in items if it.is_sensitive]
    print(f"\n[items] 共 {len(items)} 项, 其中敏感 {len(sens_items)} 项(掩码 + HMAC fingerprint):")
    for it in items[:top_n]:
        flag = "SENS" if it.is_sensitive else "    "
        val = (it.value_raw or "")[:48]
        print(f"  {flag} {it.config_key:32} = {val!r}")

    print(f"\n[bindings] 前 {top_n} 个绑定(bind_type / strategy / conf -> bind_target):")
    for b in binds[:top_n]:
        print(f"  {b.bind_type:14} {b.bind_strategy or '-':22} "
              f"{b.confidence or '-':8} -> {b.bind_target}")


def _print_config_tables(engine: Engine, db: str, top_n: int) -> None:
    """打印 Phase B/C 识别出的 DB 配置表(source_type='db_table')给人核对真伪。"""
    with engine.connect() as c:
        db_srcs = [s for s in c.execute(select(S.config_sources))
                   if s.source_type == "db_table"]
    print(f"\n[config_table] 识别出 {len(db_srcs)} 个 DB 配置表候选(人工核对是不是真配置表):")
    for s in db_srcs[:top_n]:
        print(f"  {s.owner}.{s.table_name:32} [{s.description}]")


def _build_oracle_inputs(profile, owner: str, db: str):
    """连白名单 Oracle 测试库, 返回 (oracle_tables, execute_query, client_cm)。

    oracle_tables = [{owner, table, columns}] 从 ALL_TAB_COLUMNS 聚合(供 path A 表名 +
    规则列启发)。execute_query 包成 path_b_query 期望的 (db, sql, params=...) 签名, 内部
    走 05 §8.2 execute_query 闸门(只读 + ROWNUM + bind, 红线 #4 不直连)。
    """
    from contextos.db_provider.sqlcl_mcp import connect_from_profile
    from contextos.lineage.oracle_metadata import execute_query as oracle_execute_query

    client_cm = connect_from_profile(profile)
    client = client_cm.__enter__()

    # owner 下所有表 + 列(供 path A 表名/规则列启发); 用元数据全量拉(走只读 gate)
    rows = client.query(
        "SELECT table_name, column_name FROM ALL_TAB_COLUMNS WHERE owner = :owner",
        {"owner": owner.upper()})
    cols_by_table: dict[str, list[str]] = {}
    for r in rows:
        t = (r.get("TABLE_NAME") or r.get("table_name") or "").strip()
        col = (r.get("COLUMN_NAME") or r.get("column_name") or "").strip()
        if t and col:
            cols_by_table.setdefault(t, []).append(col)
    oracle_tables = [{"owner": owner.upper(), "table": t, "columns": cols}
                     for t, cols in cols_by_table.items()]

    def execute_query(db_arg, sql, *, params=None):
        # path_b_query 调成 execute_query(db, sql, params=params); db 段仅注解, 实连走 client
        return oracle_execute_query(client, sql, params=params)

    return oracle_tables, execute_query, client_cm


def main() -> None:
    ap = argparse.ArgumentParser(description="Plan 06 配置维度手测")
    ap.add_argument("--repo", required=True, help="源码仓根/子模块路径")
    ap.add_argument("--owner", default="", help="非空 -> 连 Oracle 跑 DB 配置表识别(需 profile.toml + .env)")
    ap.add_argument("--db", default="", help="db 注解(配 --owner; 仅注解, 实连走 profile 白名单实例)")
    ap.add_argument("--top", type=int, default=15, help="每类打印前 N 条(默认 15)")
    args = ap.parse_args()

    profile, is_real = _load_profile_or_stub()
    print(f"[profile] {'config/profile.toml(真 Profile)' if is_real else '内置 stub(真实默认词表, 离线足够)'}")

    engine = make_engine("sqlite://")
    S.metadata.create_all(engine)

    cache_dir = Path(".smoke_config_cache")
    cache_dir.mkdir(exist_ok=True)

    print(f"[build] repo={args.repo}")

    if not args.owner:
        # 默认离线: 只跑 Phase A(扫文件配置 + Java 绑定)
        stats = build_file_config(args.repo, profile, engine, cache_dir)
        _print_phase_a(engine, stats, args.top)
        print("\n[note] 离线模式只跑 Phase A(文件配置)。带 --owner UPC --db CTEST 跑 DB 配置表识别。")
        return

    # 带 Oracle: 跑全 build(Phase A 文件 + Phase B 四路识别 + Phase C 确认覆盖)
    if not is_real:
        raise SystemExit("[error] --owner 需 config/profile.toml(真 Profile + Oracle 配置)。"
                         "worktree 里 profile.toml 是 gitignore 的, 从主仓拷或在主仓跑。")
    oracle_tables, execute_query, client_cm = _build_oracle_inputs(profile, args.owner, args.db)
    try:
        print(f"[oracle] owner={args.owner} 拉到 {len(oracle_tables)} 张表(供四路识别)")
        stats = build_config_dimension(
            args.repo, profile, engine, cache_dir,
            oracle_tables=oracle_tables, execute_query=execute_query,
            db=args.db, customer_id="demoproj")
    finally:
        client_cm.__exit__(None, None, None)

    _print_phase_a(engine, stats, args.top)
    _print_config_tables(engine, args.db, args.top)
    print(f"\n[note] config_tables={stats.get('config_tables', 0)} "
          f"needs_review={stats.get('config_tables_needs_review', 0)} —— 人工核对识别准不准。")


if __name__ == "__main__":
    main()
