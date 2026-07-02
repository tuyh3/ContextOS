#!/usr/bin/env python
"""需求拆解手工 smoke test —— 喂真需求(text/docx),人工判读 breakdown 抽得准不准。

为什么需要它:
  自动测试(contextos/requirement/tests/)全用 FakeLLM,测不到真实抽取质量
  (LLM 是否抽对 candidate_code_names / 8 类能力分对没 / 双语 query 通顺否)。
  这个脚本用你 profile.toml + .env 的真 LLM,真跑 pipeline,结果给人看。

怎么用:
  # 1. 喂一段文字(text 源)
  uv run python scripts/smoke_breakdown.py --text "新增动态计费批量操作,完成后发 SMS 提醒"

  # 2. 喂一个真 docx 需求文件
  uv run python scripts/smoke_breakdown.py --docx /path/to/requirement.docx

  # 3. 换 profile
  uv run python scripts/smoke_breakdown.py --text "..." --profile path/to/profile.toml

怎么判读(人工 = gold standard,见 memory feedback_human_in_the_loop_testing):
  - business_intent / key_entities:抓住主旨没?
  - candidate_code_names:有没有抠出该搜的类名/缩写(对照需求里的英文术语)?
    POC ftth-dost 就栽在这——正则抠不出 Dost,看 LLM 这次补上没。
  - matched_capabilities:8 类分对没?confidence 合理否?
  - queries.zh/en:双语 query 通顺、可拿去检索否?
  - open_questions:有没有该问的没问 / 不该降级的降级了?

安全:不打印 api_key;真调用消耗 API 额度;docx 仅本地读。
"""
from __future__ import annotations

import argparse
import sys

from contextos.llm import LLMConfigError, provider_from_profile
from contextos.profile.loader import load_profile
from contextos.requirement import breakdown


def _print_breakdown(b) -> None:
    print("\n===== RequirementBreakdown =====")
    print(f"requirement_id : {b.requirement_id}")
    print(f"assessment     : {b.assessment}        confidence: {b.confidence:.2f}")
    print(f"source_kind    : {b.source_kind}")
    print(f"raw_text       : {b.raw_text[:200]}{'...' if len(b.raw_text) > 200 else ''}")
    print(f"business_intent: {b.business_intent}")
    print(f"key_entities   : {b.key_entities}")
    print(f"actions        : {b.actions}")
    print("\nmatched_capabilities:")
    for c in b.matched_capabilities:
        print(f"  - {c.capability:22} conf={c.confidence}  {c.evidence}")
    print("\ncandidate_code_names:")
    for c in b.candidate_code_names:
        print(f"  - {c.term:24} [{c.kind}] ({c.source})  span={c.source_span!r}")
    print("\ncandidate_table_terms:")
    for c in b.candidate_table_terms:
        print(f"  - {c.term:24} [{c.kind}] ({c.source})  span={c.source_span!r}")
    print("\ncandidate_config_keys:")
    for c in b.candidate_config_keys:
        print(f"  - {c.term:24} [{c.kind}] ({c.source})  span={c.source_span!r}")
    print(f"\nqueries.zh     : {b.queries.zh}")
    print(f"queries.en     : {b.queries.en}")
    print(f"\nopen_questions : {b.open_questions}")
    print("\n(抽得准不准你自己判 —— 这是手工测试的意义)")


def main() -> None:
    ap = argparse.ArgumentParser(description="需求拆解手工 smoke test(真调 LLM)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", help="直接给一段需求文字(text 源)")
    g.add_argument("--docx", help="给一个 docx 需求文件路径(docx 源)")
    ap.add_argument("--profile", default="config/profile.toml", help="profile.toml 路径")
    args = ap.parse_args()

    print(f"[1/2] 加载 profile + 造 LLM provider: {args.profile}")
    p = load_profile(args.profile)
    try:
        llm = provider_from_profile(p)
    except LLMConfigError as e:
        print(f"      X 配置错误: {e}")
        print("      -> 检查 profile.llm.base_url/model + api_key_env 指向的变量(.env)")
        sys.exit(1)
    print(f"      OK provider = {type(llm).__name__}")

    if args.text is not None:
        raw_input, source_kind = args.text, "text"
    else:
        raw_input, source_kind = args.docx, "docx"

    print(f"[2/2] breakdown(source_kind={source_kind}) 真跑"
          "(三道 guard + scope/extract/classify/translate, 非需求会早退省调用)...")
    b = breakdown(raw_input, source_kind, llm=llm, profile=p)
    _print_breakdown(b)


if __name__ == "__main__":
    main()
