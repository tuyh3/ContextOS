#!/usr/bin/env python
"""代码搜索(桥1 code_search)手工 smoke test —— 喂真需求/搜索词,人工判读
04 在真 JDT LS workspace 上把业务词撞成的 Java 符号准不准。

为什么需要它:
  自动测试(contextos/code_intel/code_search/tests/)用 FakeSearcher,测不到真
  JDT LS workspaceSymbol 的命中质量。integration 测试只断言 CustDbUtils 一个已知
  类。这个脚本喂任意需求/词,真跑 04,把命中的 target/kind/file:line/强度打给人看。

两种模式:
  # A. 纯测 04 桥(跳过 LLM,直接给搜索词)—— 无 LLM 变量;JDT LS 每次启动都重跑
  #    Gradle import,无论第几次都 ~110-120s(见 adapter.py start() NOTE 实测口径)
  uv run python scripts/smoke_code_search.py --project demoproj-cust \
      --terms "CustDbUtils:camelcase,Route:proper_noun"

  # B. 真实端到端(需求 -> 02 DeepSeek 拆词 -> 04)—— 慢(LLM + 可能冷启)
  uv run python scripts/smoke_code_search.py --project demoproj-order \
      --text "新增动态计费批量操作,完成后发 SMS 提醒"

参数:
  --project       projects.toml 里的项目名(JDT LS 指向哪个客户项目子模块),必填
  --projects-toml 默认 data/poc/projects.toml(含 demoproj-cust/order/channel/irsc)
  --profile       默认 config/profile.toml(仅 --text 模式造 LLM 用)
  --timeout       JDT LS 启动超时秒,默认 600。每次 start() 都重跑 Gradle import,
                  无论第几次都 ~110-120s(冷启 117s / "暖启" 仍 110s,见 adapter.py
                  start() NOTE 2026-05-27 实测;缓存只省约 80s,到不了秒级)

怎么判读(人工 = gold standard,见 memory feedback_human_in_the_loop_testing):
  - 命中的 target 是不是真以该业务词命名的类/方法(而非"提到"它的噪音)?
  - name_match_strength=1.0(精确同名)还是 0.6(模糊)?精确的可信。
  - kind(CLASS/INTERFACE/METHOD/FIELD)对不对?
  - --text 模式还要看 02 拆出的 search_terms 合不合理(打印在前面)。
  - miss_reason 非 None 时:no_symbol_match(词没撞上)/no_search_terms(02 没产词)
    /requirement_rejected(02 判非需求)/jdtls_error(JDT LS 起不来)。

安全:不打印 api_key;--text 真调 LLM 消耗额度;只读客户项目源 + 本地 JDT LS。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from contextos.code_intel.code_search.input_adapter import breakdown_to_query
from contextos.code_intel.code_search.provider import search_code
from contextos.code_intel.jdtls_provider.adapter import JdtlsAdapter
from contextos.requirement.schema import CandidateName, RequirementBreakdown


def _breakdown_from_terms(terms_arg: str) -> RequirementBreakdown:
    """把 "Foo:camelcase,BAR:shouty" 直接造成 breakdown(跳过 02/LLM)。"""
    names: list[CandidateName] = []
    for chunk in terms_arg.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            term, kind = chunk.rsplit(":", 1)
            term, kind = term.strip(), kind.strip()
        else:
            term, kind = chunk, "other"
        if kind not in ("shouty", "camelcase", "proper_noun", "other"):
            print(f"      ! kind '{kind}' 非法(用 shouty/camelcase/proper_noun/other),"
                  f"按 other 处理: {term}")
            kind = "other"
        names.append(CandidateName(term=term, kind=kind, source="llm"))  # type: ignore[arg-type]
    return RequirementBreakdown(
        requirement_id="smoke", raw_text=f"(terms) {terms_arg}", source_kind="text",
        candidate_code_names=names,
    )


def _breakdown_from_text(text: str, profile_path: str) -> RequirementBreakdown:
    """真需求 -> 02 拆解(真 LLM)-> breakdown。"""
    from contextos.llm import LLMConfigError, provider_from_profile
    from contextos.profile.loader import load_profile
    from contextos.requirement import breakdown as run_breakdown

    print(f"[02] 加载 profile + 造 LLM provider: {profile_path}")
    p = load_profile(profile_path)
    try:
        llm = provider_from_profile(p)
    except LLMConfigError as e:
        print(f"      X 配置错误: {e}")
        sys.exit(1)
    print(f"      OK provider = {type(llm).__name__};真跑 02 需求拆解 ...")
    return run_breakdown(text, "text", llm=llm, profile=p)


def _print_terms(b: RequirementBreakdown) -> None:
    q = breakdown_to_query(b)
    print("\n===== 02 -> 04 搜索词(喂给 workspaceSymbol)=====")
    print(f"assessment={b.assessment} confidence={b.confidence:.2f} "
          f"matched_capability={q.matched_capability!r}")
    if not q.search_terms:
        print("  (无搜索词)")
    for t in q.search_terms:
        print(f"  - {t.term:28} [{t.kind}]")


def _print_result(r) -> None:
    print(f"\n===== 04 ProviderResult (worker_name={r.worker_name}) =====")
    print(f"score        : {r.score:.3f}    miss_reason: {r.miss_reason}")
    print(f"score_breakdown: {r.score_breakdown}")
    print(f"reasoning    : {r.reasoning}")
    cands = sorted(
        r.candidates,
        key=lambda c: c.signals.get("name_match_strength", 0.0),
        reverse=True,
    )
    print(f"\ncandidates ({len(cands)}):")
    if not cands:
        print("  (空)")
    for c in cands:
        s = c.signals
        loc = f"{s.get('file', '')}:{s.get('line_start', -1)}-{s.get('line_end', -1)}"
        print(f"  [{c.kind:9}] strength={s.get('name_match_strength', 0.0):.1f}  "
              f"{c.target}")
        print(f"      {loc}")
    print("\n(命中准不准你自己判 —— 这是手工测试的意义)")


def main() -> None:
    ap = argparse.ArgumentParser(description="代码搜索桥1 手工 smoke test(真 JDT LS)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--terms", help='直接给搜索词 "Foo:camelcase,BAR:shouty"(跳过 LLM)')
    g.add_argument("--text", help="给一段真需求(走 02 真 LLM 拆词)")
    ap.add_argument("--project", required=True, help="projects.toml 里的项目名")
    ap.add_argument("--projects-toml", default="data/poc/projects.toml")
    ap.add_argument("--profile", default="config/profile.toml")
    ap.add_argument("--timeout", type=float, default=600.0)
    args = ap.parse_args()

    toml = Path(args.projects_toml)
    if not toml.exists():
        print(f"X projects.toml 不存在: {toml}")
        sys.exit(1)

    if args.terms is not None:
        b = _breakdown_from_terms(args.terms)
    else:
        b = _breakdown_from_text(args.text, args.profile)
    _print_terms(b)

    print(f"\n[04] 启动 JDT LS workspace: project={args.project} "
          f"(每次都重跑 Gradle import,~110-120s,等两分钟正常) ...")
    adapter = JdtlsAdapter.from_config(toml, project_name=args.project)
    try:
        adapter.start(timeout_s=args.timeout)
        print("      OK LS ready;跑 search_code ...")
        r = search_code(b, adapter)
    finally:
        adapter.stop()
    _print_result(r)


if __name__ == "__main__":
    main()
