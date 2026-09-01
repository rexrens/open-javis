"""OpenAICompatAdapter — OpenAI-compatible endpoints via the official SDK.

The dsh-style adapter for DeepSeek / Qwen / Kimi / Ollama / any
OpenAI-compatible ``/chat/completions`` endpoint: implements
:meth:`LLMAdapter.stream` by serializing :class:`GenerateOptions` (dsh
messages + tool schemas) into the OpenAI chat wire format, consuming the SDK
stream, and re-emitting the raw streaming protocol (``StreamChunk``:
block-start / text-delta / reasoning-delta / tool-call-delta / block-end /
usage / finish).

Merged 2026-09-01 from the former ``javis.llm.providers.OpenAICompatProvider``
(SDK handling: lazy async client, ``stream_options`` fallback, ``_parse_delta``
tool-call accumulation) and ``javis.harness.llm_adapter`` (OpenAI serialization
+ chunk boundary emission) — the two-layer provider→adapter bridge is gone.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict
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

from javis.harness.types import (
    AbortSignal,
    BlockEndChunk,
    BlockStartChunk,
    FinishChunk,
    FinishReason,
    GenerateOptions,
    MaxTokensFinish,
    Message,
    ReasoningBlock,
    ReasoningDeltaChunk,
    StopFinish,
    TextBlock,
    TextDeltaChunk,
    TokenUsage,
    ToolCallBlock,
    ToolCallDeltaChunk,
    ToolCallsFinish,
    ToolResultBlock,
    ToolSchema,
    UsageChunk,
)
from javis.llm.adapter import LLMAdapter, LlmProviderInfo, LlmResolvedModelInfo


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


def _map_finish(finish_reason: str) -> FinishReason:
    """Provider ``finish_reason`` → harness ``FinishReason``."""
    reason = (finish_reason or "").strip()
    if reason == "tool_calls":
        return ToolCallsFinish()
    if reason == "length":
        return MaxTokensFinish()
    return StopFinish()


class OpenAICompatAdapter(LLMAdapter):
    """OpenAI-compatible endpoints via the official openai SDK (async path).

    Lazy ``AsyncOpenAI`` client; retries are handled by the SDK
    (``max_retries``); ``stream_options`` falls back when a provider rejects
    it (400). Optional disk cache replays the full chunk sequence of a
    previously-cached request.
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
        cache_response: bool = False,
        cache_dir: str | Path | None = None,
        cache_ttl: int | None = None,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_context_tokens = max_context_tokens
        self.cache_response = cache_response
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl
        self.max_retries = max_retries
        self._aclient: Any = None  # AsyncOpenAI (lazy)

    # -- provider metadata ----------------------------------------------------

    def set_model(self, model: str) -> None:
        """Switch the model this adapter serves (``AgentEngine.set_model``)."""
        self.model = model

    def provider_info(self, provider: str) -> LlmProviderInfo:
        return LlmProviderInfo(id=provider, name=provider)

    async def resolve_model(
        self,
        provider: str,
        model: str,
        signal: AbortSignal | None = None,
    ) -> LlmResolvedModelInfo:
        del signal
        return LlmResolvedModelInfo(
            provider=provider,
            id=model,
            name=model,
            context_window=self.max_context_tokens,
            default_max_tokens=self.max_tokens,
        )

    # -- core stream ----------------------------------------------------------

    def _ensure_aclient(self) -> Any:
        from openai import AsyncOpenAI

        if self._aclient is None:
            self._aclient = AsyncOpenAI(
                api_key=self.api_key or "sk-missing",
                base_url=self.base_url,
                max_retries=self.max_retries,
            )
        return self._aclient

    def _base_params(self, options: GenerateOptions) -> dict[str, Any]:
        """Build SDK params from a GenerateOptions request.

        Request fields with a value override the constructor defaults (None
        keeps the default); ``options.system`` becomes the ``system`` message.
        """
        params: dict[str, Any] = {
            "model": options.model or self.model,
            "messages": _to_openai_messages(options),
            "stream_options": {"include_usage": True},
            "temperature": (
                options.temperature if options.temperature is not None else self.temperature
            ),
            "max_tokens": options.max_tokens if options.max_tokens is not None else self.max_tokens,
        }
        if options.tools:
            params["tools"] = self._format_tools([_to_openai_tool(t) for t in options.tools])
        return params

    def _format_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sort tools by name → stable request prefix → prompt caching hits."""
        return sorted(tools, key=lambda t: (t.get("function") or {}).get("name", ""))

    async def stream(self, options: GenerateOptions) -> AsyncIterator[Any]:
        """Emit the raw streaming protocol for one request (dsh adapter)."""
        cache_key = self._cache_key(options)
        if self.cache_response:
            cached = self._get_cached(cache_key)
            if cached is not None:
                for chunk in cached:
                    yield chunk
                return

        params = self._base_params(options)
        try:
            stream = await self._ensure_aclient().chat.completions.create(**params, stream=True)
        except BadRequestError:
            params.pop("stream_options", None)
            stream = await self._ensure_aclient().chat.completions.create(**params, stream=True)

        tc_map: dict[int, dict[str, Any]] = {}
        text_buffer: list[str] = []
        reasoning_buffer: list[str] = []
        text_open = reasoning_open = False
        emitted: set[str] = set()
        index = 0
        collected: list[Any] = []

        async for chunk in stream:
            content, reasoning, finish_reason, prompt_tok, completion_tok = _parse_openai_chunk(
                chunk, tc_map
            )
            if content:
                if not text_open:
                    collected.append(BlockStartChunk(index=index, block_type="text"))
                    yield BlockStartChunk(index=index, block_type="text")
                    text_open = True
                text_buffer.append(content)
                collected.append(TextDeltaChunk(index=index, text=content))
                yield TextDeltaChunk(index=index, text=content)
            if reasoning:
                if not reasoning_open:
                    collected.append(BlockStartChunk(index=index, block_type="reasoning"))
                    yield BlockStartChunk(index=index, block_type="reasoning")
                    reasoning_open = True
                reasoning_buffer.append(reasoning)
                collected.append(ReasoningDeltaChunk(index=index, text=reasoning))
                yield ReasoningDeltaChunk(index=index, text=reasoning)
            # Provider tool calls arrive as cumulative snapshots per chunk:
            # diff against what we already emitted and emit one block per call.
            for tc_id, name, arguments in _tool_call_snapshots(tc_map):
                if tc_id and tc_id not in emitted:
                    emitted.add(tc_id)
                    collected.append(BlockStartChunk(index=index, block_type="tool-call"))
                    yield BlockStartChunk(index=index, block_type="tool-call")
                    collected.append(
                        ToolCallDeltaChunk(
                            index=index, id=tc_id, name=name, arguments_delta=arguments
                        )
                    )
                    yield ToolCallDeltaChunk(index=index, id=tc_id, name=name, arguments_delta=arguments)
                    collected.append(
                        BlockEndChunk(
                            index=index,
                            block=ToolCallBlock(id=tc_id, name=name, arguments=arguments),
                        )
                    )
                    yield BlockEndChunk(
                        index=index,
                        block=ToolCallBlock(id=tc_id, name=name, arguments=arguments),
                    )
                    index += 1
            if prompt_tok or completion_tok:
                collected.append(
                    UsageChunk(
                        usage=TokenUsage(
                            input_tokens=prompt_tok, output_tokens=completion_tok
                        )
                    )
                )
                yield UsageChunk(
                    usage=TokenUsage(input_tokens=prompt_tok, output_tokens=completion_tok)
                )
            if finish_reason:
                collected.append(FinishChunk(reason=_map_finish(finish_reason)))
                yield FinishChunk(reason=_map_finish(finish_reason))

        # Close open content blocks with the fully assembled block (normal
        # exhaustion only: a mid-stream exception leaves blocks open and
        # ``LlmRuntime._adapter_stream`` turns the failure into a terminal
        # finish).
        if text_open:
            collected.append(BlockEndChunk(index=0, block=TextBlock(text="".join(text_buffer))))
            yield BlockEndChunk(index=0, block=TextBlock(text="".join(text_buffer)))
        if reasoning_open:
            collected.append(
                BlockEndChunk(index=0, block=ReasoningBlock(text="".join(reasoning_buffer)))
            )
            yield BlockEndChunk(index=0, block=ReasoningBlock(text="".join(reasoning_buffer)))

        if self.cache_response:
            self._save_cached(cache_key, collected)

    # -- optional disk cache --------------------------------------------------

    def _cache_key(self, options: GenerateOptions) -> str:
        payload = json.dumps(
            {
                "model": options.model or self.model,
                "messages": _to_openai_messages(options),
                "tools": [_to_openai_tool(t) for t in (options.tools or ())],
                "max_tokens": options.max_tokens,
                "temperature": options.temperature,
            },
            sort_keys=True,
            default=str,
        )
        return md5(payload.encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        base = self.cache_dir or (Path.home() / ".javis" / "cache" / "llm")
        return base / f"{key}.json"

    def _get_cached(self, key: str) -> list[Any] | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if self.cache_ttl is not None and data.get("ts", 0) + self.cache_ttl < _now():
            return None
        try:
            return [_chunk_from_dict(d) for d in data["chunks"]]
        except (KeyError, TypeError, ValueError):
            return None

    def _save_cached(self, key: str, chunks: list[Any]) -> None:
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"ts": int(_now()), "chunks": [_chunk_to_dict(c) for c in chunks]},
            ensure_ascii=False,
        )
        try:
            path.write_text(payload, encoding="utf-8")
        except OSError:
            pass


def _now() -> float:
    import time

    return time.time()


# ---------------------------------------------------------------------------
# OpenAI chunk parsing (SDK chunk → deltas; tool calls accumulate in tc_map)
# ---------------------------------------------------------------------------


def _parse_openai_chunk(
    chunk: Any,
    tc_map: dict[int, dict[str, Any]],
) -> tuple[str, str, str, int, int]:
    """Parse one streaming SDK chunk into (content, reasoning, finish_reason,
    prompt_tokens, completion_tokens). Tool-call fragments accumulate in
    ``tc_map`` (streaming tool calls span multiple chunks)."""
    prompt_tok = completion_tok = 0
    finish_reason = ""
    if chunk.usage:
        prompt_tok = chunk.usage.prompt_tokens or 0
        completion_tok = chunk.usage.completion_tokens or 0

    content = ""
    reasoning: str = ""
    if chunk.choices:
        delta = chunk.choices[0].delta
        finish_reason = chunk.choices[0].finish_reason or ""
        if delta.content:
            content = delta.content
        # DeepSeek-R1 / Kimi expose reasoning in delta.reasoning_content
        reasoning = getattr(delta, "reasoning_content", None) or ""
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                # Initialize only on first appearance, then accumulate across
                # chunks (resetting here would wipe id/name → 400 on the API).
                if idx not in tc_map:
                    tc_map[idx] = {"id": "", "name": "", "args": ""}
                if tc_delta.id:
                    tc_map[idx]["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tc_map[idx]["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        tc_map[idx]["args"] += tc_delta.function.arguments
    return content, reasoning, finish_reason, prompt_tok, completion_tok


def _tool_call_snapshots(tc_map: dict[int, dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Current cumulative tool-call snapshots, in index order."""
    out: list[tuple[str, str, str]] = []
    for idx in sorted(tc_map):
        raw = tc_map[idx]
        try:
            args = json.dumps(json.loads(raw["args"]), ensure_ascii=False) if raw["args"] else "{}"
        except json.JSONDecodeError:
            args = raw["args"]
        out.append((raw["id"], raw["name"], args))
    return out


# ---------------------------------------------------------------------------
# OpenAI serialization (GenerateOptions → OpenAI chat format)
# ---------------------------------------------------------------------------


def _to_openai_messages(options: GenerateOptions) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if options.system:
        messages.append({"role": "system", "content": options.system})
    messages.extend(_to_openai_message(m) for m in options.messages)
    return messages


def _to_openai_message(message: Message) -> dict[str, Any]:
    """Convert one harness message to OpenAI chat dict (the wire format)."""
    if message.role == "user":
        return {"role": "user", "content": _message_text(message)}
    if message.role == "assistant":
        entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
        calls = [b for b in message.content if isinstance(b, ToolCallBlock)]
        if calls:
            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in calls
            ]
        return entry
    if message.role == "tool":
        block = message.content[0] if message.content else None
        if isinstance(block, ToolResultBlock):
            text = "".join(b.text for b in block.content if isinstance(b, TextBlock))
            return {
                "role": "tool",
                "tool_call_id": block.tool_call_id,
                "content": text,
            }
        return {"role": "tool", "tool_call_id": getattr(message, "call_id", ""), "content": ""}
    return {"role": message.role, "content": message.text}


def _message_text(message: Message) -> str:
    """Text content of a user/assistant message (tool results are separate)."""
    return "".join(b.text for b in message.content if isinstance(b, TextBlock))


def _to_openai_tool(schema: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": schema.name,
            "description": schema.description,
            "parameters": schema.parameters,
        },
    }


# ---------------------------------------------------------------------------
# Chunk serialization for the disk cache
# ---------------------------------------------------------------------------


def _chunk_to_dict(chunk: Any) -> dict[str, Any]:
    return asdict(chunk)


def _chunk_from_dict(data: dict[str, Any]) -> Any:
    kind = data.get("type")
    if kind == "block-start":
        return BlockStartChunk(index=data["index"], block_type=data["block_type"])
    if kind == "text-delta":
        return TextDeltaChunk(index=data["index"], text=data["text"])
    if kind == "reasoning-delta":
        return ReasoningDeltaChunk(index=data["index"], text=data["text"])
    if kind == "tool-call-delta":
        return ToolCallDeltaChunk(
            index=data["index"],
            id=data.get("id", ""),
            name=data.get("name"),
            arguments_delta=data.get("arguments_delta", ""),
        )
    if kind == "block-end":
        block = data["block"]
        return BlockEndChunk(index=data["index"], block=_block_from_dict(block))
    if kind == "usage":
        usage = data["usage"]
        return UsageChunk(
            usage=TokenUsage(
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cache_read_tokens=usage.get("cache_read_tokens"),
                cache_write_tokens=usage.get("cache_write_tokens"),
                reasoning_tokens=usage.get("reasoning_tokens"),
            )
        )
    if kind == "finish":
        return FinishChunk(reason=_finish_from_dict(data["reason"]))
    raise ValueError(f"unknown cached chunk type {kind!r}")


def _block_from_dict(data: dict[str, Any]) -> Any:
    block_type = data.get("type")
    if block_type == "text":
        return TextBlock(text=data["text"])
    if block_type == "reasoning":
        return ReasoningBlock(text=data["text"])
    if block_type == "tool-call":
        return ToolCallBlock(
            id=data["id"], name=data["name"], arguments=data["arguments"]
        )
    if block_type == "tool-result":
        return ToolResultBlock(
            tool_call_id=data["tool_call_id"],
            content=tuple(data.get("content", ())),
            is_error=data.get("is_error", False),
        )
    raise ValueError(f"unknown cached block type {block_type!r}")


def _finish_from_dict(data: dict[str, Any]) -> FinishReason:
    kind = data.get("kind")
    if kind == "stop":
        return StopFinish()
    if kind == "tool-calls":
        return ToolCallsFinish()
    if kind == "max-tokens":
        return MaxTokensFinish()
    raise ValueError(f"unsupported cached finish kind {kind!r}")


__all__ = [
    "OpenAICompatAdapter",
    "_to_openai_message",
    "_to_openai_tool",
    "is_fallback_trigger",
]
