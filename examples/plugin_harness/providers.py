"""Provider seam for the plugin harness — a tiny, self-contained LLM adapter.

The harness engine never talks to the OpenAI SDK (or any vendor). It only
knows :class:`ChatProvider`: one ``complete`` call per agent round, receiving
OpenAI-style messages plus tool schemas, and streaming text/reasoning through
callbacks.

Two implementations ship with this example:

- :class:`ScriptedProvider` — an offline, deterministic "model" used for demos
  and tests (no API key, no network). It replays a script of turns so the
  full tool-call loop can be exercised end to end.
- :class:`OpenAICompatChatProvider` — a streaming adapter over the official
  OpenAI SDK for any OpenAI-compatible endpoint (DeepSeek / Qwen / Kimi /
  Ollama ...).

A real harness would add its own providers here (Anthropic, LiteLLM, local
models, ...) without touching the engine.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

TextSink = Callable[[str], None]


@dataclass
class ToolCallDraft:
    """One tool call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResult:
    """Outcome of one model round."""

    content: str
    tool_calls: list[ToolCallDraft] = field(default_factory=list)
    usage: dict[str, int] | None = None  # {"input_tokens": n, "output_tokens": n}


class ChatProvider(Protocol):
    """A model behind the harness: one ``complete`` per agent round.

    ``messages`` are OpenAI-style dicts (system / user / assistant / tool);
    ``tools`` are OpenAI function schemas (``Tool.schema()``). Streamed text
    and reasoning arrive through the callbacks; the return value carries the
    final content and any tool calls.
    """

    model: str

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_text: TextSink,
        on_reasoning: TextSink,
    ) -> ProviderResult: ...


# ---------------------------------------------------------------------------
# ScriptedProvider — offline deterministic model
# ---------------------------------------------------------------------------


@dataclass
class ScriptedTurn:
    """One scripted model reply (offline demo)."""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCallDraft] = field(default_factory=list)
    output_tokens: int = 12


def _chunks(text: str, size: int = 6) -> list[str]:
    """Split text into small chunks so streaming events are exercised."""
    return [text[i : i + size] for i in range(0, len(text), size)]


class ScriptedProvider:
    """Deterministic offline model: replays a script of turns.

    Content/reasoning are streamed in small chunks so the harness event
    pipeline (``AgentTextDelta`` / ``AgentReasoningDelta``) is exercised for
    real. After the script is exhausted it answers with a short closing text.
    """

    def __init__(self, script: list[ScriptedTurn], model: str = "scripted-demo") -> None:
        self.model = model
        self._script = list(script)
        self._cursor = 0

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_text: TextSink,
        on_reasoning: TextSink,
    ) -> ProviderResult:
        del messages, tools
        if self._cursor >= len(self._script):
            text = "(scripted demo: no more turns)"
            for chunk in _chunks(text):
                on_text(chunk)
            return ProviderResult(content=text, usage={"input_tokens": 0, "output_tokens": 4})
        turn = self._script[self._cursor]
        self._cursor += 1
        if turn.reasoning:
            for chunk in _chunks(turn.reasoning):
                on_reasoning(chunk)
        for chunk in _chunks(turn.content):
            on_text(chunk)
        return ProviderResult(
            content=turn.content,
            tool_calls=turn.tool_calls,
            usage={"input_tokens": 8, "output_tokens": turn.output_tokens},
        )


# ---------------------------------------------------------------------------
# OpenAICompatChatProvider — streaming OpenAI-compatible adapter
# ---------------------------------------------------------------------------


class OpenAICompatChatProvider:
    """Streaming OpenAI-compatible adapter (DeepSeek / Qwen / Kimi / Ollama ...).

    A thin wrapper over the official ``openai`` SDK's ``AsyncOpenAI`` client;
    the harness engine itself stays SDK-free.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key or ""
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._client: Any = None

    def close(self) -> None:
        """Release the SDK client reference (called on plugin dispose)."""
        self._client = None

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_text: TextSink,
        on_reasoning: TextSink,
    ) -> ProviderResult:
        from openai import AsyncOpenAI

        if self._client is None:
            self._client = AsyncOpenAI(api_key=self._api_key or "sk-missing", base_url=self._base_url)

        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            params["tools"] = tools
        if self._max_tokens is not None:
            params["max_tokens"] = self._max_tokens

        try:
            stream = await self._client.chat.completions.create(**params)
        except Exception:  # noqa: BLE001 — some providers reject stream_options
            params.pop("stream_options", None)
            stream = await self._client.chat.completions.create(**params)

        content_parts: list[str] = []
        tool_slots: dict[int, dict[str, str]] = {}
        usage: Any = None
        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                on_reasoning(reasoning)
            if delta.content:
                on_text(delta.content)
                content_parts.append(delta.content)
            if delta.tool_calls:
                for call in delta.tool_calls:
                    slot = tool_slots.setdefault(
                        call.index,
                        {"id": f"call_{call.index}", "name": "", "arguments": ""},
                    )
                    if call.id:
                        slot["id"] = call.id
                    if call.function and call.function.name:
                        slot["name"] += call.function.name
                    if call.function and call.function.arguments:
                        slot["arguments"] += call.function.arguments

        drafts = [
            ToolCallDraft(
                id=slot["id"],
                name=slot["name"],
                arguments=json.loads(slot["arguments"] or "{}"),
            )
            for slot in tool_slots.values()
        ]
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        return ProviderResult(
            content="".join(content_parts),
            tool_calls=drafts,
            usage=(
                {"input_tokens": int(input_tokens), "output_tokens": int(output_tokens)}
                if input_tokens is not None and output_tokens is not None
                else None
            ),
        )


__all__ = [
    "ChatProvider",
    "OpenAICompatChatProvider",
    "ProviderResult",
    "ScriptedProvider",
    "ScriptedTurn",
    "ToolCallDraft",
]
