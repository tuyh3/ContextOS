"""provider_from_profile -- 从 profile.llm 构建 LLMProvider。

v1:base_url + model + api_key 齐全 -> OpenAICompatProvider;
测试 / 离线场景:传 override=FakeLLM(...) 直接短路。
多 provider 路由(translation_provider / fallback_provider)留 Plan 02 translate.py。

API key 走环境变量(红线:密钥绝不进 profile.toml)。支持 .env 文件:
load_dotenv() 把仓根 .env 注入环境(与 db_provider/sqlcl_mcp.py 一致的凭据模式;
.env 已 gitignore)。load_dotenv 默认不覆盖已 export 的同名变量。
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from contextos.llm.base import LLMConfigError, LLMProvider
from contextos.llm.openai_compat import OpenAICompatProvider


def provider_from_profile(profile, *, override: LLMProvider | None = None) -> LLMProvider:
    if override is not None:
        return override
    cfg = profile.llm
    if not cfg.base_url or not cfg.model:
        raise LLMConfigError(
            "profile.llm 需要 base_url + model 才能构建真实 client;"
            "测试 / 离线请传 override=FakeLLM(...)"
        )
    load_dotenv()  # 注入仓根 .env(若有);已 export 的同名变量优先,不被覆盖
    api_key = os.environ.get(cfg.api_key_env)
    if not api_key:
        raise LLMConfigError(f"环境变量 {cfg.api_key_env} 未设置(profile.llm.api_key_env)")
    return OpenAICompatProvider(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=api_key,
        temperature=cfg.temperature,
        timeout=cfg.timeout_seconds,
        max_retries=cfg.max_retries,
    )
