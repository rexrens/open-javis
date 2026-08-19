"""LLM provider layer — unified interface over LLM APIs.

Design: spec/llm-provider.md v2.

Four methods (sync/async × streaming/non-streaming) with a single abstract
entry point (``achat_stream``); everything else derives from it:

- ``achat_stream``  — abstract, the real implementation point (javis is
  async-only and the TUI needs streaming)
- ``achat``         — base-class aggregation of ``achat_stream``
- ``chat_stream`` / ``chat`` — sync variants; base class raises
  ``NotImplementedError`` (javis main path is async), providers may override

Key decisions from the spec:
- Retries come from the OpenAI SDK (``max_retries``); error classification
  (``is_fallback_trigger``) exists only for fallback decisions
- ``_format_tools`` sorts tools by name → stable request prefix → prompt
  caching hits
- Optional disk cache for non-streaming responses
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

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

# ---------------------------------------------------------------------------
# error classification — used ONLY for fallback decisions (SDK does retries)
# ---------------------------------------------------------------------------


def is_fallback_trigger(exc: Exception) -> bool:
    """Should a failing primary provider switch to the fallback?

    - rate limit / server errors / timeouts / connection → switch
    - 4xx client errors → keep (config/credential problems won't be fixed
      by another provider)
    - unknown → switch conservatively
    """
    if isinstance(exc, (RateLimitError, InternalServerError, APITimeoutError, APIConnectionError)):
        return True
    return not isinstance(exc, APIStatusError)  # 4xx → keep; unknown → switch


# ---------------------------------------------------------------------------
# data models
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_content: str | None = None  # DeepSeek-R1 / Kimi
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"

    @property
    def message(self) -> dict:
        """Convert to OpenAI message format for appending to history."""
        msg: dict = {"role": "assistant", "content": self.content or None}
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
        """Aggregate a streaming delta into this response."""
        return LLMResponse(
            content=self.content + other.content,
            tool_calls=self.tool_calls + other.tool_calls,
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


# ---------------------------------------------------------------------------
# base class
# ---------------------------------------------------------------------------


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
    async def achat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMResponse]:
        """Async streaming: yield one delta ``LLMResponse`` per chunk.

        ``on_token`` fires for content deltas; ``on_reasoning`` fires for
        reasoning/thinking deltas (DeepSeek-R1, Qwen3 thinking, …).
        """

    # -- derived: async non-streaming --------------------------------------

    async def achat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Async non-streaming. Base default: aggregate ``achat_stream``."""
        cache_key = self._cache_key(messages, tools, **kwargs)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        merged = LLMResponse()
        async for delta in self.achat_stream(
            messages, tools, on_token=on_token, on_reasoning=on_reasoning, **kwargs
        ):
            merged = merged.merge(delta)
        self._track_usage(merged)
        self._save_cached(cache_key, merged)
        return merged

    # -- sync variants (javis main path is async) --------------------------

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> Iterator[LLMResponse]:
        """Sync streaming. Not implemented in the base — override in subclass."""
        raise NotImplementedError(
            "chat_stream is not implemented; javis runs async (achat_stream). "
            "Override in the subclass if sync is needed."
        )

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        **kwargs: Any,
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

    def _format_tools(self, tools: list[dict] | None) -> list[dict]:
        """Sort tools by name → stable request prefix → prompt caching hits.

        (OpenAI/Anthropic/Gemini prompt caching all require stable prefixes.)
        """
        if not tools:
            return []
        return sorted(tools, key=lambda t: (t.get("function") or {}).get("name", ""))

    # -- optional disk cache -----------------------------------------------

    def _cache_key(self, messages: list[dict], tools: list[dict] | None, **kwargs: Any) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "max_tokens": kwargs.get("max_tokens"),
                "temperature": kwargs.get("temperature"),
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


# ---------------------------------------------------------------------------
# ScriptedProvider — deterministic offline playback (tests/demos)
# ---------------------------------------------------------------------------


class ScriptedProvider(LLMProvider):
    """Plays back a fixed script of LLMResponse turns, one per achat call.

    Running out of turns is an error (a broken loop shows up immediately).
    """

    def __init__(self, script: list[LLMResponse], model: str = "scripted-demo", **kwargs: Any):
        super().__init__(model, **kwargs)
        self._turns = list(script)

    async def achat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMResponse]:
        del messages, tools, kwargs
        if not self._turns:
            raise RuntimeError("ScriptedProvider ran out of turns")
        resp = self._turns.pop(0)
        if on_token and resp.content:
            on_token(resp.content)
        if on_reasoning and resp.reasoning_content:
            on_reasoning(resp.reasoning_content)
        self.total_completion_tokens += len(resp.content.split())
        yield resp

    def chat(self, messages, tools=None, *, on_token=None, on_reasoning=None, **kwargs) -> LLMResponse:
        """Scripted sync: pop the next turn and deliver it whole."""
        del messages, tools, kwargs
        if not self._turns:
            raise RuntimeError("ScriptedProvider ran out of turns")
        resp = self._turns.pop(0)
        if on_token and resp.content:
            on_token(resp.content)
        if on_reasoning and resp.reasoning_content:
            on_reasoning(resp.reasoning_content)
        self.total_completion_tokens += len(resp.content.split())
        return resp


# ---------------------------------------------------------------------------
# OpenAICompatProvider — OpenAI-compatible endpoints (DeepSeek/Qwen/Kimi/…)
# ---------------------------------------------------------------------------


class OpenAICompatProvider(LLMProvider):
    """OpenAI-compatible endpoints via the official openai SDK.

    Lazy dual clients (sync ``OpenAI`` + async ``AsyncOpenAI``); retries are
    handled by the SDK (``max_retries``); ``stream_options`` falls back when
    a provider rejects it (400).
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_context_tokens: int = 128_000,
        **kwargs: Any,
    ):
        super().__init__(model, **kwargs)
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_context_tokens = max_context_tokens
        self._client: Any = None  # OpenAI (lazy)
        self._aclient: Any = None  # AsyncOpenAI (lazy)

    def _ensure_client(self):
        from openai import OpenAI

        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key or "sk-missing",
                base_url=self.base_url,
                max_retries=self.max_retries,
            )
        return self._client

    def _ensure_aclient(self):
        from openai import AsyncOpenAI

        if self._aclient is None:
            self._aclient = AsyncOpenAI(
                api_key=self.api_key or "sk-missing",
                base_url=self.base_url,
                max_retries=self.max_retries,
            )
        return self._aclient

    def _base_params(self, messages: list[dict], tools: list[dict] | None) -> dict:
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream_options": {"include_usage": True},
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            params["tools"] = self._format_tools(tools)
        return params

    async def achat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMResponse]:
        params = self._base_params(messages, tools)
        params.update(kwargs)
        try:
            stream = await self._ensure_aclient().chat.completions.create(**params, stream=True)
        except BadRequestError:
            params.pop("stream_options", None)
            stream = await self._ensure_aclient().chat.completions.create(**params, stream=True)
        async for chunk in stream:
            yield _parse_delta(chunk, on_token, on_reasoning)

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> Iterator[LLMResponse]:
        params = self._base_params(messages, tools)
        params.update(kwargs)
        try:
            stream = self._ensure_client().chat.completions.create(**params, stream=True)
        except BadRequestError:
            params.pop("stream_options", None)
            stream = self._ensure_client().chat.completions.create(**params, stream=True)
        for chunk in stream:
            yield _parse_delta(chunk, on_token)

    async def achat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Non-streaming override: one JSON request is faster than SSE."""
        cache_key = self._cache_key(messages, tools, **kwargs)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        params = self._base_params(messages, tools)
        params.update(kwargs)
        try:
            response = await self._ensure_aclient().chat.completions.create(**params)
        except BadRequestError:
            params.pop("stream_options", None)
            response = await self._ensure_aclient().chat.completions.create(**params)
        result = _parse_completion(response)
        if on_token and result.content:
            on_token(result.content)
        if on_reasoning and result.reasoning_content:
            on_reasoning(result.reasoning_content)
        self._track_usage(result)
        self._save_cached(cache_key, result)
        return result

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Sync non-streaming."""
        params = self._base_params(messages, tools)
        params.update(kwargs)
        try:
            response = self._ensure_client().chat.completions.create(**params)
        except BadRequestError:
            params.pop("stream_options", None)
            response = self._ensure_client().chat.completions.create(**params)
        result = _parse_completion(response)
        if on_token and result.content:
            on_token(result.content)
        if on_reasoning and result.reasoning_content:
            on_reasoning(result.reasoning_content)
        self._track_usage(result)
        return result


# ---------------------------------------------------------------------------
# chunk / completion parsing (shared by sync & async paths)
# ---------------------------------------------------------------------------


def _parse_delta(
    chunk: Any,
    on_token: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> LLMResponse:
    """Parse one streaming chunk into a delta LLMResponse."""
    prompt_tok = 0
    completion_tok = 0
    finish_reason = ""
    if chunk.usage:
        prompt_tok = chunk.usage.prompt_tokens or 0
        completion_tok = chunk.usage.completion_tokens or 0

    content = ""
    reasoning: str | None = None
    tc_map: dict[int, dict] = {}

    if chunk.choices:
        delta = chunk.choices[0].delta
        finish_reason = chunk.choices[0].finish_reason or ""
        if delta.content:
            content = delta.content
        # DeepSeek-R1 / Kimi expose reasoning in delta.reasoning_content
        reasoning = getattr(delta, "reasoning_content", None)
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                tc_map[idx] = {"id": tc_delta.id or "", "name": "", "args": ""}
                if tc_delta.function:
                    if tc_delta.function.name:
                        tc_map[idx]["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        tc_map[idx]["args"] = tc_delta.function.arguments

    parsed: list[ToolCall] = []
    for idx in sorted(tc_map):
        raw = tc_map[idx]
        try:
            args = json.loads(raw["args"]) if raw["args"] else {}
        except json.JSONDecodeError:
            args = {}
        parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))

    if on_token and content:
        on_token(content)
    if on_reasoning and reasoning:
        on_reasoning(reasoning)

    return LLMResponse(
        content=content,
        tool_calls=parsed,
        reasoning_content=reasoning,
        prompt_tokens=prompt_tok,
        completion_tokens=completion_tok,
        finish_reason=finish_reason,
    )


def _parse_completion(response: Any) -> LLMResponse:
    """Parse a non-streaming ChatCompletion into a full LLMResponse."""
    usage = getattr(response, "usage", None)
    prompt_tok = getattr(usage, "prompt_tokens", 0) or 0
    completion_tok = getattr(usage, "completion_tokens", 0) or 0

    content = ""
    reasoning: str | None = None
    finish_reason = ""
    tc_map: dict[int, dict] = {}

    if response.choices:
        choice = response.choices[0]
        finish_reason = choice.finish_reason or ""
        msg = choice.message
        if msg.content:
            content = msg.content
        reasoning = getattr(msg, "reasoning_content", None)
        if msg.tool_calls:
            for i, tc in enumerate(msg.tool_calls):
                tc_map[i] = {
                    "id": tc.id,
                    "name": tc.function.name,
                    "args": tc.function.arguments or "",
                }

    parsed: list[ToolCall] = []
    for idx in sorted(tc_map):
        raw = tc_map[idx]
        try:
            args = json.loads(raw["args"]) if raw["args"] else {}
        except json.JSONDecodeError:
            args = {}
        parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))

    return LLMResponse(
        content=content,
        tool_calls=parsed,
        reasoning_content=reasoning,
        prompt_tokens=prompt_tok,
        completion_tokens=completion_tok,
        finish_reason=finish_reason,
    )


__all__ = [
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatProvider",
    "ScriptedProvider",
    "ToolCall",
    "estimated_cost",
    "is_fallback_trigger",
]
