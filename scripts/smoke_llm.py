#!/usr/bin/env python
"""LLM 层手工 smoke test —— 让人(不是 CI)动手验"真能连上大模型"。

为什么需要它:
  自动测试(contextos/llm/tests/)全程用 FakeLLM,不碰真端点 —— 确定、免费、离线,
  但**测不到**真实连通性(profile 配错 / key 失效 / 端点变了 / 模型 id 无效 / 网络)。
  这个脚本就是补这一环:用你 profile.toml + .env 的真配置,真打 LLM 端点,结果给人看。

怎么用:
  # 1. 完整 smoke(造 provider + complete + structured 三步,适合改完 LLM 层一键复验)
  uv run python scripts/smoke_llm.py

  # 2. 自己出题(临时 complete,看模型对任意 prompt 的原始回应)
  uv run python scripts/smoke_llm.py "用一句话解释什么是动态计费"

  # 3. 换个 profile 文件
  uv run python scripts/smoke_llm.py --profile path/to/profile.toml

怎么判读(人工 = gold standard,见 memory feedback_human_in_the_loop_testing):
  - 三步都打 ✅ = LLM 层真连通,可往上盖 Plan 02。
  - complete 报 LLMHTTPError = 看错误码:401/403 多半 key 问题;404/400 多半 base_url/model 问题。
  - structured 报 LLMStructuredError = 模型产的 JSON 反复不合 schema(小模型常见;换大模型或简化 schema)。
  - 回应内容对不对、质量好不好 —— **你自己判**,脚本不评分(这是手工测试的意义)。

安全:不打印 api_key;prompt 是无意义测试句;真调用会消耗你的 API 额度。
"""
from __future__ import annotations

import argparse
import sys

from pydantic import BaseModel

from contextos.llm import (
    LLMConfigError,
    LLMHTTPError,
    LLMStructuredError,
    provider_from_profile,
)
from contextos.profile.loader import load_profile


class _Sentiment(BaseModel):
    """structured() 演示用的小 schema。"""

    label: str   # positive / negative / neutral
    confidence: float


def _build(profile_path: str):
    print(f"[1/?] 加载 profile: {profile_path}")
    p = load_profile(profile_path)
    print(f"      provider={p.llm.provider}  base_url={p.llm.base_url}  model={p.llm.model}")
    try:
        llm = provider_from_profile(p)
    except LLMConfigError as e:
        print(f"      X 配置错误: {e}")
        print("      -> 检查 profile.llm.base_url/model 填了没,api_key_env 指向的变量(.env 或 export)有没有值")
        sys.exit(1)
    print(f"      OK provider = {type(llm).__name__}")
    return llm


def _ad_hoc(llm, prompt: str) -> None:
    print(f"\n[2/2] 你的 prompt -> complete()")
    print(f"      Q: {prompt}")
    try:
        out = llm.complete(prompt)
    except LLMHTTPError as e:
        print(f"      X HTTP 失败: {e}")
        sys.exit(2)
    print(f"      A: {out}")
    print("\n(内容对不对你自己判)")


def _full_smoke(llm) -> None:
    print("\n[2/3] complete() 纯文本")
    try:
        out = llm.complete("用一句话回答:1+1 等于几?")
    except LLMHTTPError as e:
        print(f"      X HTTP 失败: {e}")
        print("      -> 401/403=key;404/400=base_url 或 model")
        sys.exit(2)
    print(f"      A: {out!r}")

    print("\n[3/3] structured() 强制 JSON + pydantic 校验(失败会自纠重试)")
    try:
        s = llm.structured(
            "判断这句话情绪:'这个产品真的太好用了'。confidence 给 0-1。",
            _Sentiment,
        )
    except (LLMHTTPError, LLMStructuredError) as e:
        print(f"      X 失败: {e}")
        sys.exit(3)
    print(f"      label={s.label!r}  confidence={s.confidence}  (类型校验通过)")

    print("\nOK 三步全过 —— LLM 层真端点连通 + structured 自纠都正常。")


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM 层手工 smoke test(真调端点)")
    ap.add_argument("prompt", nargs="?", help="给了就只跑这个 prompt 的 complete();不给跑完整 3 步 smoke")
    ap.add_argument("--profile", default="config/profile.toml", help="profile.toml 路径")
    args = ap.parse_args()

    llm = _build(args.profile)
    if args.prompt:
        _ad_hoc(llm, args.prompt)
    else:
        _full_smoke(llm)


if __name__ == "__main__":
    main()
