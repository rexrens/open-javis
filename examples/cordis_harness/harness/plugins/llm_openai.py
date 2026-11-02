"""Register an OpenAI-compatible provider route on ``ctx.llm``."""

from __future__ import annotations

from pydantic import BaseModel

from harness.llm_openai import OpenAiAdapter, OpenAiSettings

name = "llm-openai"
inject = ["llm"]


class Config(BaseModel):
    """Connection facts for one OpenAI-compatible route."""

    provider: str = "openai"
    baseUrl: str = "https://api.deepseek.com/v1"
    apiKeyEnv: str = "OPENAI_API_KEY"
    fallbackApiKeyEnv: str | None = "DEEPSEEK_API_KEY"
    models: list[str] = ["deepseek-v4-flash"]
    timeoutMs: int = 300_000
    includeUsage: bool = True
    maxTokens: int | None = None
    reasoningEffort: str | None = None


def apply(ctx, config: Config):
    settings = OpenAiSettings(
        provider=config.provider,
        base_url=config.baseUrl,
        api_key_env=config.apiKeyEnv,
        fallback_api_key_env=config.fallbackApiKeyEnv,
        models=list(config.models),
        timeout_s=config.timeoutMs / 1000,
        include_usage=config.includeUsage,
        max_tokens=config.maxTokens,
        reasoning_effort=config.reasoningEffort,
    )
    return ctx.get("llm").register_adapter([config.provider], OpenAiAdapter(settings))
