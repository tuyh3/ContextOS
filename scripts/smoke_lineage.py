#!/usr/bin/env python3
"""Plan 05 手测脚本: build 真 repo -> 打印血缘统计 + provider 查询结果给人核对。

用法:
  # 离线 build + 查询(无 Oracle):
  uv run python scripts/smoke_lineage.py --repo /path/to/your/project/cust \
      --term PM_OFFER --dao "/impl/,/src/main/"
  # 带 Oracle 元数据富化(需 config/profile.toml + .env + VPN):
  uv run python scripts/smoke_lineage.py --repo .../cust --term PM_OFFER --owner UPC

设计: 自动测试用 Fake/小 fixture(快、确定); 手测用真实客户项目 + 真 Oracle, 人核对
"抽得准不准"(gold-standard 人工, 见 [[feedback_human_in_the_loop_testing]])。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from contextos.profile.schema import CodeConfig, DaoSqlPattern, TablesConfig
from contextos.requirement.schema import CandidateTableTerm, RequirementBreakdown
from contextos.storage.db import make_engine
from contextos.lineage import store
from contextos.lineage.pipeline import build_lineage
from contextos.lineage.provider import search_lineage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="源码仓根/子模块路径")
    ap.add_argument("--term", action="append", default=[], help="candidate_table_term(可多次)")
    ap.add_argument("--dao", default="", help="DAO 路径标识, 逗号分隔(如 /impl/,/src/main/)")
    ap.add_argument("--owner", default="", help="非空 -> 用 Oracle 富化(需 profile.toml + .env)")
    args = ap.parse_args()

    engine = make_engine("sqlite://")
    store.create_all(engine)

    dao_patterns = []
    if args.dao:
        dao_patterns = [DaoSqlPattern(path_contains=[s for s in args.dao.split(",") if s],
                                      conjunction="all")]
    code = CodeConfig(dao_sql_patterns=dao_patterns)

    # 可选 Oracle 富化(走 11b refresh_metadata: 全量快照覆盖 + 盖时间戳)
    if args.owner:
        try:
            import tomllib
            from datetime import datetime, timezone

            from contextos.db_provider.sqlcl_mcp import connect_from_profile
            from contextos.lineage.oracle_metadata import refresh_metadata
            from contextos.profile.schema import Profile
            toml_path = Path("config/profile.toml")
            with open(toml_path, "rb") as f:
                profile = Profile(**tomllib.load(f))
            now_iso = datetime.now(timezone.utc).isoformat()
            with connect_from_profile(profile) as client:
                # 裁决 5: db_name 走 instance_alias(默认空), 不传测试实例名误导生产消费者
                summary = refresh_metadata(
                    client, engine, owners=[args.owner], now=now_iso,
                    db_name=profile.oracle.instance_alias.get(profile.oracle.allowed_instances[0], ""))
            print(f"[Oracle] metadata refreshed: {summary}")
        except Exception as exc:  # noqa: BLE001
            print(f"[Oracle] 富化失败(降级离线): {type(exc).__name__}: {exc}")

    print(f"[build] repo={args.repo}")
    stats = build_lineage(Path(args.repo), code, TablesConfig(), engine)
    print(f"[build] stats={stats}")

    edges = store.all_edges(engine)
    print("[build] 前 10 条边:")
    for e in edges[:10]:
        print(f"  {e['relation_type']:14} {e['src_table']} -> {e['dst_table']} "
              f"({e['recovery_mode']}, conf={e['confidence']}, ev={e['evidence_count']})")

    if args.term:
        b = RequirementBreakdown(
            requirement_id="smoke", raw_text="smoke", source_kind="text",
            candidate_table_terms=[CandidateTableTerm(term=t, kind="entity", source="llm")
                                   for t in args.term])
        r = search_lineage(b, engine)
        print(f"\n[provider] terms={args.term}")
        print(f"[provider] worker={r.worker_name} score={r.score} miss={r.miss_reason}")
        print(f"[provider] score_breakdown={r.score_breakdown}")
        for c in r.candidates[:15]:
            print(f"  {c.target}  [{c.signals['relation_type']}] "
                  f"ev={c.signals['evidence_count']} mode={c.signals['recovery_mode']}")


if __name__ == "__main__":
    main()
