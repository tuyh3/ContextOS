# scripts/smoke_orchestrate.py
"""08 数据流编排真端到端手测(主仓跑;需 .env + config/profile.toml + 已 build 的 05/06 库
+ 03 物化语料 + JDT LS workspace)。**不在 worktree 拷密钥**(见 feedback_never_rm_secrets_from_main)。

用法(主仓):
  uv run python scripts/smoke_orchestrate.py \
      --text "新增动态计费批量操作,完成后发 SMS 提醒" \
      --materialized-dir <03 物化语料目录> \
      --project demoproj-cust --artifact-root data/poc

prereq:
  - config/profile.toml + 仓根 .env(DEEPSEEK_API_KEY)
  - 05 lineage + 06 config 已 build 落 engine_from_profile 的库(否则两桥 miss)
  - 03 物化语料目录(03a materialize 产物)
  - projects.toml 指客户项目 cust 子模块(JDT LS workspace)
"""
from __future__ import annotations

import argparse
from pathlib import Path

from contextos.code_intel.jdtls_provider.adapter import JdtlsAdapter
from contextos.llm import provider_from_profile
from contextos.orchestrator.pipeline import analyze
from contextos.orchestrator.registry import build_default_registry
from contextos.profile import load_profile, validate_profile
from contextos.recall.rag_provider import RagProvider
from contextos.recall.reranker import make_reranker
from contextos.storage.db import engine_from_profile


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--profile", default="config/profile.toml")
    ap.add_argument("--projects-toml", default="data/poc/projects.toml")
    ap.add_argument("--project", default="demoproj-cust")
    ap.add_argument("--materialized-dir", required=True, help="03 物化语料目录")
    ap.add_argument("--artifact-root", default="data/poc",
                    help="data_dir;artifact writer 写到 <root>/runs/<run_id>(别传 .../runs 否则双 runs/)")
    args = ap.parse_args()

    profile = load_profile(Path(args.profile))
    validate_profile(profile, check_paths=False)
    llm = provider_from_profile(profile)

    searcher = JdtlsAdapter.from_config(Path(args.projects_toml), args.project)
    searcher.start()
    try:
        engine = engine_from_profile(profile)   # 05+06 共用持久库(需先 build)
        rag_provider = RagProvider(args.materialized_dir, make_reranker(profile.rag), profile.rag)
        registry = build_default_registry(
            searcher=searcher, rag_provider=rag_provider,
            lineage_engine=engine, config_engine=engine, llm=llm)

        impact = analyze(args.text, "text", registry, llm=llm, profile=profile,
                         artifact_root=Path(args.artifact_root))

        print(f"\n=== Impact Map: {impact.requirement_id} ===")
        print(f"summary: {impact.requirement_summary}")
        print(f"dimension_status: {impact.dimension_status}")
        print(f"evidence_items: {len(impact.evidence_items)}")
        for e in sorted(impact.evidence_items, key=lambda x: x.confidence, reverse=True)[:20]:
            folded = " [folded]" if e.metadata.get("folded") else ""
            print(f"  [{e.confidence_tier}] {e.confidence:.3f} {e.kind:12} {e.target} "
                  f"({e.change_type}){folded}")
    finally:
        if hasattr(searcher, "stop"):
            searcher.stop()


if __name__ == "__main__":
    main()
