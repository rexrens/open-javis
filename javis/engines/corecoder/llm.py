"""LLM provider implementations for the corecoder engine.

The provider *contract* lives in ``javis.contracts.llm`` — ``LLMProvider``
(single abstract method ``achat_stream``), the data models (``LLMRequest`` /
``LLMResponse`` / ``ToolCall``) and ``estimated_cost`` — and is re-exported
here so existing imports keep working. This module holds the SDK-dependent
pieces: error classification (``is_fallback_trigger``, used only for
fallback decisions — the SDK does retries) and the concrete providers
(``OpenAICompatProvider`` / ``ScriptedProvider``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from javis.contracts.llm import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ToolCall,
    estimated_cost,
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
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> AsyncIterator[LLMResponse]:
        """Play back the next scripted turn; request content and sampling
        params are ignored (a scripted model doesn't respond to them)."""
        del request, extra_body
        if not self._turns:
            raise RuntimeError("ScriptedProvider ran out of turns")
        resp = self._turns.pop(0)
        if on_reasoning and resp.reasoning_content:
            on_reasoning(resp.reasoning_content)
        if on_token and resp.content:
            on_token(resp.content)
        self.total_completion_tokens += len(resp.content.split())
        yield resp

    def chat(
        self,
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Scripted sync: pop the next turn and deliver it whole."""
        del request, extra_body
        if not self._turns:
            raise RuntimeError("ScriptedProvider ran out of turns")
        resp = self._turns.pop(0)
        if on_reasoning and resp.reasoning_content:
            on_reasoning(resp.reasoning_content)
        if on_token and resp.content:
            on_token(resp.content)
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

    def _ensure_client(self) -> Any:
        from openai import OpenAI

        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key or "sk-missing",
                base_url=self.base_url,
                max_retries=self.max_retries,
            )
        return self._client

    def _ensure_aclient(self) -> Any:
        from openai import AsyncOpenAI

        if self._aclient is None:
            self._aclient = AsyncOpenAI(
                api_key=self.api_key or "sk-missing",
                base_url=self.base_url,
                max_retries=self.max_retries,
            )
        return self._aclient

    def _base_params(self, request: LLMRequest, extra_body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build SDK params from a request.

        LLMRequest fields with a value override the constructor defaults
        (None keeps the default); ``extra_body`` is merged last so
        vendor-only passthrough fields win.
        """
        params: dict[str, Any] = {
            "model": self.model,
            "messages": request.messages,
            "stream_options": {"include_usage": True},
            "temperature": (
                request.temperature if request.temperature is not None else self.temperature
            ),
            "max_tokens": request.max_tokens if request.max_tokens is not None else self.max_tokens,
        }
        if request.tools:
            params["tools"] = self._format_tools(request.tools)
        if request.stop is not None:
            params["stop"] = request.stop
        if request.top_p is not None:
            params["top_p"] = request.top_p
        if request.seed is not None:
            params["seed"] = request.seed
        if request.response_format is not None:
            params["response_format"] = request.response_format
        if extra_body:
            params.update(extra_body)
        return params

    async def achat_stream(
        self,
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> AsyncIterator[LLMResponse]:
        params = self._base_params(request, extra_body)
        try:
            stream = await self._ensure_aclient().chat.completions.create(**params, stream=True)
        except BadRequestError:
            params.pop("stream_options", None)
            stream = await self._ensure_aclient().chat.completions.create(**params, stream=True)
        tc_map: dict[int, dict[str, Any]] = {}
        async for chunk in stream:
            yield _parse_delta(chunk, on_token, on_reasoning, tc_map)

    def chat_stream(
        self,
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> Iterator[LLMResponse]:
        params = self._base_params(request, extra_body)
        try:
            stream = self._ensure_client().chat.completions.create(**params, stream=True)
        except BadRequestError:
            params.pop("stream_options", None)
            stream = self._ensure_client().chat.completions.create(**params, stream=True)
        for chunk in stream:
            yield _parse_delta(chunk, on_token)

    def chat(
        self,
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Sync non-streaming."""
        params = self._base_params(request, extra_body)
        try:
            response = self._ensure_client().chat.completions.create(**params)
        except BadRequestError:
            params.pop("stream_options", None)
            response = self._ensure_client().chat.completions.create(**params)
        result = _parse_completion(response)
        if on_reasoning and result.reasoning_content:
            on_reasoning(result.reasoning_content)
        if on_token and result.content:
            on_token(result.content)
        self._track_usage(result)
        return result


# ---------------------------------------------------------------------------
# chunk / completion parsing (shared by sync & async paths)
# ---------------------------------------------------------------------------


def _parse_delta(
    chunk: Any,
    on_token: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
    tc_map: dict[int, dict[str, Any]] | None = None,
) -> LLMResponse:
    """Parse one streaming chunk into a delta LLMResponse.

    ``tc_map`` carries tool-call accumulation across chunks (streaming tool
    calls span multiple chunks); pass a fresh dict per stream to keep the
    id/name/arguments assembled correctly. When None, a per-call dict is
    used (stateless single-chunk parsing).
    """
    """Parse one streaming chunk into a delta LLMResponse."""
    prompt_tok = 0
    completion_tok = 0
    finish_reason = ""
    if chunk.usage:
        prompt_tok = chunk.usage.prompt_tokens or 0
        completion_tok = chunk.usage.completion_tokens or 0

    content = ""
    reasoning: str | None = None
    if tc_map is None:
        tc_map = {}

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
                # Streaming tool calls span multiple chunks: initialize only on
                # first appearance, then accumulate across chunks. Resetting here
                # would wipe the id/name from earlier chunks (400 on the API).
                if idx not in tc_map:
                    tc_map[idx] = {"id": "", "name": "", "args": ""}
                if tc_delta.id:
                    tc_map[idx]["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tc_map[idx]["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        tc_map[idx]["args"] += tc_delta.function.arguments

    parsed: list[ToolCall] = []
    for idx in sorted(tc_map):
        raw = tc_map[idx]
        try:
            args = json.loads(raw["args"]) if raw["args"] else {}
        except json.JSONDecodeError:
            args = {}
        parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))

    if on_reasoning and reasoning:
        on_reasoning(reasoning)
    if on_token and content:
        on_token(content)

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
    tc_map: dict[int, dict[str, Any]] = {}

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
    "LLMRequest",
    "LLMResponse",
    "OpenAICompatProvider",
    "ScriptedProvider",
    "ToolCall",
    "estimated_cost",
    "is_fallback_trigger",
]
