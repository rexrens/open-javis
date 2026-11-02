"""Model pricing table + cost estimation (javis-specific; no dsh counterpart).

Extracted from the former ``LLMProvider`` base (``javis.contracts.llm``,
removed 2026-09-01) into the LLM layer so adapters and the engine can share
it without a provider contract.

Table refreshed 2026-09 from each vendor's public pricing page (per 1M
tokens, USD, standard cache-miss / short-context rates):
- openai.com/api/pricing (GPT-5.6 family, 2026)
- platform.claude.com/docs/en/about-claude/pricing (Claude 5 / Opus 4.8, 2026-05)
- api-docs.deepseek.com (V4-Flash / V4-Pro; deepseek-chat / deepseek-reasoner
  retired 2026-07-24)
- docs.qwencloud.com / alibabacloud.com model-studio (Qwen3.8 Max, 2026-08)
- platform.kimi.ai (Kimi K3, 2026-07)
- ai.google.dev/gemini-api/docs/pricing (Gemini 2.5)

Prices are approximate and change often; ``estimated_cost`` returns None for
models not in the table rather than guessing.
"""

from __future__ import annotations

# pricing per million tokens: (input, output)
_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI — GPT-5.6 flagships (short-context tier; long-context is 2x in / 1.5x out)
    "gpt-5.6-sol": (4, 20),
    "gpt-5.6-terra": (2, 12),
    "gpt-5.6-luna": (0.2, 1.2),
    # OpenAI — GPT-5.x current flagships
    "gpt-5.5": (5, 30),
    "gpt-5.4": (2.5, 15),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4-nano": (0.2, 1.25),
    "o4-mini": (1.1, 4.4),
    # OpenAI — previous gen (still widely used)
    "gpt-4.1": (2, 8),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4o": (2.5, 10),
    "gpt-4o-mini": (0.15, 0.6),
    # Anthropic Claude
    "claude-fable-5": (10, 50),
    "claude-mythos-5": (10, 50),  # limited availability
    "claude-opus-5": (5, 25),
    "claude-opus-4-8": (5, 25),
    "claude-sonnet-4-6": (3, 15),
    "claude-haiku-4-5": (1, 5),
    # DeepSeek — V4 generation (chat/reasoner retired 2026-07-24)
    "deepseek-v4-pro": (0.435, 0.87),
    "deepseek-v4-flash": (0.14, 0.28),
    # Alibaba Qwen
    "qwen3.8-max": (2, 6),
    "qwen3-max": (0.78, 3.9),
    "qwen3-plus": (0.26, 0.78),
    "qwen-max": (0.78, 3.9),
    # Moonshot Kimi
    "kimi-k3": (3, 15),
    "kimi-k2.6": (0.95, 4),
    "kimi-k2.5": (0.6, 3),
    # Google Gemini (standard ≤200K-context tier)
    "gemini-2.5-pro": (1.25, 10),
    "gemini-2.5-flash": (0.3, 2.5),
}


def estimated_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Rough cost estimate in USD. Returns None if model not in pricing table."""
    pricing = _PRICING.get(model)
    if not pricing:
        return None
    input_rate, output_rate = pricing
    return (
        prompt_tokens * input_rate / 1_000_000
        + completion_tokens * output_rate / 1_000_000
    )


__all__ = ["estimated_cost"]
