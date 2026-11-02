"""OpenAI-compatible chat-completions adapter.

One adapter serves DeepSeek's official endpoint and any gateway that speaks
OpenAI chat completions: the base URL, credential reference and model ids are
configuration. Provider-specific details stay here; the loop only sees the
harness stream vocabulary.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import openai
from openai import AsyncOpenAI

from .llm import LlmAdapter, LlmError
from .types import (
    ContentBlock,
    GenerateOptions,
    LlmFailure,
    Message,
    ToolSchema,
    block_end,
    block_start,
    blocks_text,
    finish_chunk,
    reasoning_delta,
    text_delta,
    tool_call_delta,
    usage_chunk,
)


@dataclass
class OpenAiSettings:
    """Connection and call facts for one OpenAI-compatible route."""

    provider: str = "openai"
    base_url: str = "https://api.deepseek.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    fallback_api_key_env: str | None = "DEEPSEEK_API_KEY"
    models: list[str] = field(default_factory=lambda: ["deepseek-v4-flash"])
    timeout_s: float = 300.0
    include_usage: bool = True
    max_tokens: int | None = None
    reasoning_effort: str | None = None


def resolve_api_key(settings: OpenAiSettings) -> str | None:
    """Read the credential from the configured environment variables."""
    names = [settings.api_key_env]
    if settings.fallback_api_key_env:
        names.append(settings.fallback_api_key_env)
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def to_wire_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Project harness messages onto the chat-completions wire shape."""
    wire: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        blocks: list[ContentBlock] = message.get("content") or []
        if role in ("system", "user"):
            text = blocks_text(blocks)
            if text:
                wire.append({"role": role, "content": text})
            continue
        if role == "assistant":
            calls = [block for block in blocks if block.get("type") == "tool-call"]
            entry: dict[str, Any] = {"role": "assistant", "content": blocks_text(blocks) or None}
            if calls:
                entry["tool_calls"] = [
                    {
                        "id": call.get("id", ""),
                        "type": "function",
                        "function": {"name": call.get("name", ""), "arguments": call.get("arguments", "")},
                    }
                    for call in calls
                ]
            if entry["content"] is None and not calls:
                continue
            wire.append(entry)
            continue
        if role == "tool":
            for block in blocks:
                if block.get("type") != "tool-result":
                    continue
                text = blocks_text(block.get("content") or [])
                if block.get("isError"):
                    text = f"error: {text}"
                wire.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("toolCallId", ""),
                        "content": text,
                    }
                )
    return wire


def to_wire_tools(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def to_wire_usage(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    total = getattr(usage, "total_tokens", None)
    if prompt is None and completion is None:
        return None
    result: dict[str, int] = {"inputTokens": int(prompt or 0), "outputTokens": int(completion or 0)}
    if total is not None:
        result["totalTokens"] = int(total)
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    if cached:
        result["cacheReadTokens"] = int(cached)
    return result


def map_error(error: BaseException) -> LlmFailure:
    """Map a provider or transport failure onto a stable harness code."""
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return LlmFailure(message=str(error), code="AUTH", status=getattr(error, "status_code", None))
    if isinstance(error, openai.RateLimitError):
        return LlmFailure(message=str(error), code="RATE_LIMIT", status=getattr(error, "status_code", None))
    if isinstance(error, openai.APIConnectionError):
        return LlmFailure(message=str(error), code="CONNECTION")
    if isinstance(error, openai.APIStatusError):
        status = getattr(error, "status_code", None)
        code = {400: "BAD_REQUEST", 404: "NOT_FOUND", 408: "TIMEOUT", 422: "BAD_REQUEST"}.get(
            status, "SERVER_ERROR" if status and status >= 500 else "STATUS_ERROR"
        )
        return LlmFailure(message=str(error), code=code, status=status)
    if isinstance(error, openai.APIError):
        return LlmFailure(message=str(error), code="API_ERROR", status=getattr(error, "status_code", None))
    return LlmFailure(message=f"{type(error).__name__}: {error}", code="ERROR")


def map_finish_reason(reason: str | None, saw_tool_calls: bool) -> str:
    if reason in ("tool_calls", "function_call"):
        return "tool-calls"
    if reason == "length":
        return "max-tokens"
    if reason is None:
        return "tool-calls" if saw_tool_calls else "stop"
    return "stop"


class OpenAiAdapter(LlmAdapter):
    """Stream one OpenAI-compatible chat-completions call as harness chunks."""

    def __init__(self, settings: OpenAiSettings):
        self.settings = settings

    def list_models(self, provider: str) -> list[str]:
        return list(self.settings.models)

    # -- wire ---------------------------------------------------------------

    def _client(self, api_key: str) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_s,
            max_retries=0,
        )

    def _request(self, options: GenerateOptions, include_usage: bool) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": options.model or (self.settings.models[0] if self.settings.models else ""),
            "messages": to_wire_messages(options.messages),
            "stream": True,
        }
        tools = to_wire_tools(options.tools)
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"
        max_tokens = options.max_tokens or self.settings.max_tokens
        if max_tokens:
            request["max_tokens"] = max_tokens
        effort = options.reasoning_effort or self.settings.reasoning_effort
        if effort:
            request["reasoning_effort"] = effort
        if include_usage:
            request["stream_options"] = {"include_usage": True}
        return request

    async def _open_stream(self, client: AsyncOpenAI, options: GenerateOptions) -> Any:
        """Create the stream, retrying once without ``stream_options`` when the
        endpoint rejects it (many gateways do)."""
        try:
            return await self._create(client, self._request(options, self.settings.include_usage))
        except openai.BadRequestError as error:
            if self.settings.include_usage and "stream_options" in str(error):
                return await self._create(client, self._request(options, False))
            raise

    async def _create(self, client: AsyncOpenAI, request: dict[str, Any]) -> Any:
        """Issue the wire request (the seam tests replace with a stub)."""
        return await client.chat.completions.create(**request)

    # -- stream -------------------------------------------------------------

    async def stream(self, options: GenerateOptions) -> AsyncIterator[dict[str, Any]]:
        api_key = resolve_api_key(self.settings)
        if not api_key:
            names = ", ".join(filter(None, [self.settings.api_key_env, self.settings.fallback_api_key_env]))
            yield finish_chunk(
                "error",
                LlmFailure(
                    message=f"no API key found; set one of: {names}",
                    code="MISSING_CREDENTIAL",
                ),
            )
            return

        client = self._client(api_key)
        indexes: dict[str, int] = {}
        texts: dict[int, str] = {}
        reasonings: dict[int, str] = {}
        tool_calls: dict[int, dict[str, str]] = {}
        order: list[int] = []
        usage: dict[str, int] | None = None
        finish_reason: str | None = None

        def open_block(key: str, block_type: str) -> int:
            if key not in indexes:
                index = len(order)
                indexes[key] = index
                order.append(index)
                return index
            return indexes[key]

        stream: Any = None
        try:
            stream = await self._open_stream(client, options)
            async for chunk in stream:
                chunk_usage = to_wire_usage(getattr(chunk, "usage", None))
                if chunk_usage is not None:
                    usage = chunk_usage
                for choice in getattr(chunk, "choices", None) or []:
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue
                    if getattr(choice, "finish_reason", None):
                        finish_reason = choice.finish_reason
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        index = open_block("reasoning", "reasoning")
                        if index not in reasonings:
                            reasonings[index] = ""
                            yield block_start(index, "reasoning")
                        reasonings[index] += reasoning
                        yield reasoning_delta(index, reasoning)
                    content = getattr(delta, "content", None)
                    if content:
                        index = open_block("text", "text")
                        if index not in texts:
                            texts[index] = ""
                            yield block_start(index, "text")
                        texts[index] += content
                        yield text_delta(index, content)
                    for call in getattr(delta, "tool_calls", None) or []:
                        key = f"tool:{getattr(call, 'index', 0)}"
                        index = open_block(key, "tool-call")
                        state = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if getattr(call, "id", None):
                            state["id"] = call.id
                        function = getattr(call, "function", None)
                        if function is not None and getattr(function, "name", None):
                            state["name"] = function.name
                        delta_args = getattr(function, "arguments", None) if function is not None else None
                        if delta_args:
                            state["arguments"] += delta_args
                            yield tool_call_delta(index, state["id"], delta_args, state["name"] or None)
        except asyncio.CancelledError:
            yield finish_chunk("aborted", LlmFailure(message="caller cancelled the request", code="ABORTED"))
            raise
        except LlmError as error:
            yield finish_chunk("error", error.failure())
            return
        except Exception as error:  # noqa: BLE001 - provider/transport failures
            yield finish_chunk("error", map_error(error))
            return
        finally:
            for closer in (getattr(stream, "close", None), client.close):
                if closer is None:
                    continue
                try:
                    await closer()
                except BaseException:  # pragma: no cover - best-effort cleanup
                    pass

        # Close every open block, in first-seen order.
        for index in order:
            if index in texts:
                yield block_end(index, {"type": "text", "text": texts[index]})
            elif index in reasonings:
                yield block_end(index, {"type": "reasoning", "text": reasonings[index]})
            else:
                state = tool_calls.get(index, {"id": "", "name": "", "arguments": ""})
                if state["id"] and state["name"]:
                    yield block_end(
                        index,
                        {"type": "tool-call", "id": state["id"], "name": state["name"], "arguments": state["arguments"]},
                    )
        if usage is not None:
            yield usage_chunk(usage)
        yield finish_chunk(map_finish_reason(finish_reason, bool(tool_calls)))
