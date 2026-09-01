"""LLM provider contract — the stable interface implemented by every provider.

The contract is deliberately SDK-free: a plugin or third-party provider only
needs to implement ``achat_stream`` (the single abstract method); the other
three call styles (``achat`` / ``chat_stream`` / ``chat``) derive from it.
Data models (``LLMRequest`` / ``LLMResponse`` / ``ToolCall``) are the wire
payloads exchanged with the provider layer.

Everything here is pure: stdlib only, no openai SDK, no javis-internal
dependencies — ``javis.engines.harness.providers`` re-exports these names so
existing imports keep working.

Design decisions (spec/llm-provider.md):
- D1: ``achat_stream`` is the only abstract method; sync variants raise
  ``NotImplementedError`` in the base (javis is async-only) and may be
  overridden for provider-specific optimization.
- D2: ``on_token`` / ``on_reasoning`` stay as observer callbacks, not
  LLMRequest fields.
- D3: streaming yields incremental deltas; ``LLMResponse.merge`` aggregates.
- D11: sampling params are explicit ``LLMRequest`` fields (None = use the
  provider's constructor default); ``extra_body`` is a transport-level
  passthrough that never enters the cache key.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from hashlib import md5
from pathlib import Path
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMRequest:
    """一次 LLM 调用的请求内容（模型输入 = 内容 + 采样参数）。

    采样参数字段为 None 表示"不覆盖"，使用 provider 构造时的默认值
    （如 OpenAICompatProvider(temperature=0.0, max_tokens=4096)）。
    非 None 则本次调用覆盖。
    """

    messages: list[dict[str, Any]]  # 对话历史（OpenAI Chat 格式）
    tools: list[dict[str, Any]] | None = None  # 工具 schema
    max_tokens: int | None = None
    temperature: float | None = None
    stop: list[str] | None = None
    top_p: float | None = None
    seed: int | None = None
    response_format: dict[str, Any] | None = None


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_content: str | None = None  # DeepSeek-R1 / Kimi
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"

    @property
    def message(self) -> dict[str, Any]:
        """Convert to OpenAI message format for appending to history."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg

    def merge(self, other: LLMResponse) -> LLMResponse:
        """Aggregate a streaming delta into this response.

        ``content`` / ``reasoning_content`` are per-chunk increments (concatenate);
        ``tool_calls`` are cumulative snapshots per chunk (last non-empty wins).
        """
        return LLMResponse(
            content=self.content + other.content,
            tool_calls=other.tool_calls or self.tool_calls,
            reasoning_content=(self.reasoning_content or "")
            + (other.reasoning_content or "")
            or None,
            prompt_tokens=other.prompt_tokens or self.prompt_tokens,
            completion_tokens=other.completion_tokens or self.completion_tokens,
            finish_reason=other.finish_reason or self.finish_reason,
        )


# pricing per million tokens: (input, output)
# sources: openai.com/api/pricing, api-docs.deepseek.com, platform.claude.com,
#          platform.moonshot.ai, alibabacloud.com/help/en/model-studio
_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI - current flagships
    "gpt-5.5": (5, 30),
    "gpt-5.4": (2.5, 15),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4-nano": (0.2, 1.25),
    "o4-mini": (1.1, 4.4),
    # OpenAI - previous gen (still widely used)
    "gpt-4.1": (2, 8),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4o": (2.5, 10),
    "gpt-4o-mini": (0.15, 0.6),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # Anthropic Claude
    "claude-opus-4-6": (5, 25),
    "claude-sonnet-4-6": (3, 15),
    "claude-haiku-4-5": (1, 5),
    # Alibaba Qwen
    "qwen3-max": (0.78, 3.9),
    "qwen3-plus": (0.26, 0.78),
    "qwen-max": (0.78, 3.9),
    # Moonshot Kimi
    "kimi-k2.5": (0.6, 3),
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


class LLMProvider(ABC):
    """Unified LLM interface.

    Subclasses implement ``achat_stream``; the other three methods derive
    from it (or may be overridden for provider-specific optimization).
    """

    def __init__(
        self,
        model: str,
        *,
        cache_response: bool = False,
        cache_dir: str | Path | None = None,
        cache_ttl: int | None = None,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self.cache_response = cache_response
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl
        self.max_retries = max_retries
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    # -- the one abstract method -------------------------------------------

    @abstractmethod
    def achat_stream(
        self,
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> AsyncIterator[LLMResponse]:
        """Async streaming: yield one delta ``LLMResponse`` per chunk.

        ``on_token`` fires for content deltas; ``on_reasoning`` fires for
        reasoning/thinking deltas (DeepSeek-R1, Qwen3 thinking, …).
        ``extra_body`` is a provider-specific passthrough (never part of the
        cache key) for vendor-only request fields not covered by LLMRequest.
        """

    # -- derived: async non-streaming --------------------------------------

    async def achat(
        self,
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Async non-streaming. Base default: aggregate ``achat_stream``."""
        cache_key = self._cache_key(request)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        merged = LLMResponse()
        async for delta in self.achat_stream(
            request, extra_body=extra_body, on_token=on_token, on_reasoning=on_reasoning
        ):
            merged = merged.merge(delta)
        self._track_usage(merged)
        self._save_cached(cache_key, merged)
        return merged

    # -- sync variants (javis main path is async) --------------------------

    def chat_stream(
        self,
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> Iterator[LLMResponse]:
        """Sync streaming. Not implemented in the base — override in subclass."""
        raise NotImplementedError(
            "chat_stream is not implemented; javis runs async (achat_stream). "
            "Override in the subclass if sync is needed."
        )

    def chat(
        self,
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Sync non-streaming. Not implemented in the base — override in subclass."""
        raise NotImplementedError(
            "chat is not implemented; javis runs async (achat). "
            "Override in the subclass if sync is needed."
        )

    # -- shared helpers ----------------------------------------------------

    def _track_usage(self, response: LLMResponse) -> None:
        self.total_prompt_tokens += response.prompt_tokens
        self.total_completion_tokens += response.completion_tokens

    @property
    def estimated_cost(self) -> float | None:
        """Rough cost estimate in USD. None if model not in pricing table."""
        return estimated_cost(self.model, self.total_prompt_tokens, self.total_completion_tokens)

    def _format_tools(self, tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Sort tools by name → stable request prefix → prompt caching hits.

        (OpenAI/Anthropic/Gemini prompt caching all require stable prefixes.)
        """
        if not tools:
            return []
        return sorted(tools, key=lambda t: (t.get("function") or {}).get("name", ""))

    # -- optional disk cache -----------------------------------------------

    def _cache_key(self, request: LLMRequest) -> str:
        """Cache key = hash of everything that can change the model output.

        ``extra_body`` is deliberately excluded: it is a transport-level
        passthrough (vendor-only fields), not model input. If a passthrough
        field ever affects output, promote it to an explicit LLMRequest field.
        """
        payload = json.dumps(
            {
                "model": self.model,
                "messages": request.messages,
                "tools": request.tools,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "stop": request.stop,
                "top_p": request.top_p,
                "seed": request.seed,
                "response_format": request.response_format,
            },
            sort_keys=True,
            default=str,
        )
        return md5(payload.encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        base = self.cache_dir or (Path.home() / ".javis" / "cache" / "llm")
        return base / f"{key}.json"

    def _get_cached(self, key: str) -> LLMResponse | None:
        if not self.cache_response:
            return None
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if self.cache_ttl is not None and time.time() - data.get("ts", 0) > self.cache_ttl:
            return None
        return LLMResponse(**data["response"])

    def _save_cached(self, key: str, response: LLMResponse) -> None:
        if not self.cache_response:
            return
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "ts": int(time.time()),
                "response": {
                    "content": response.content,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                    "reasoning_content": response.reasoning_content,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "finish_reason": response.finish_reason,
                },
            },
            ensure_ascii=False,
        )
        try:
            path.write_text(payload, encoding="utf-8")
        except OSError:
            pass


__all__ = [
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "ToolCall",
    "estimated_cost",
]
