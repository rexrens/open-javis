"""JavisLLMAdapter — bridges ``javis.contracts.llm.LLMProvider`` onto the
harness core's LLM seam.

The core agent loop consumes ``harness.core.llm.LLM``: ``prepare_call``
(exact-model adapter resolution) + ``stream(GenerateOptions)`` yielding
``StreamChunk`` (block-start / text-delta / reasoning-delta /
tool-call-delta / block-end / usage / finish). Real javis providers
(``OpenAICompatProvider`` for DeepSeek/Qwen/Kimi/Ollama, …) expose
``achat_stream(LLMRequest, on_token, on_reasoning)`` and yield delta
``LLMResponse`` objects.

The adapter:

- converts the loop's ``GenerateOptions`` (dsh messages + tool schemas)
  into an ``LLMRequest`` (OpenAI chat format);
- consumes ``achat_stream`` and re-emits the raw streaming protocol —
  reasoning/content deltas become ``*-delta`` chunks with block boundaries,
  tool calls are diffed from the provider's cumulative per-chunk snapshots
  into one ``tool-call`` block each, usage becomes ``usage``, and the
  provider's ``finish_reason`` maps onto ``FinishChunk``;
- ``prepare_call`` surfaces the provider's constructor defaults
  (``maxTokens``) and context window as adapter facts for the loop's
  request-header logging.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from javis.contracts.llm import LLMProvider, LLMRequest

from .core.contracts import (
    AbortSignal,
    BlockEndChunk,
    BlockStartChunk,
    FinishChunk,
    FinishReason,
    GenerateOptions,
    LlmCallConfig,
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
from .core.llm import PreparedCall


def _map_finish(finish_reason: str) -> FinishReason:
    """Provider ``finish_reason`` → dsh ``FinishReason`` (dsh llm/types.ts)."""
    reason = (finish_reason or "").strip()
    if reason == "tool_calls":
        return ToolCallsFinish()
    if reason == "length":
        return MaxTokensFinish()
    return StopFinish()


class JavisLLMAdapter:
    """Adapt a :class:`javis.contracts.llm.LLMProvider` to the core's LLM seam."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_context_tokens: int = 128_000,
    ) -> None:
        self._provider = provider
        self._max_context_tokens = max_context_tokens
        self._index = 0

    # -- provider access -----------------------------------------------------

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    def set_model(self, model: str) -> None:
        """Switch the underlying provider's model (mutable on LLMProvider)."""
        self._provider.model = model

    # -- core LLM seam -------------------------------------------------------

    def prepare_call(self, config: LlmCallConfig, signal: AbortSignal | None = None) -> PreparedCall:
        """Exact-model adapter resolution (dsh ``prepareCall``).

        The provider owns its ``maxTokens`` constructor default and advertises
        a context window — surfaced as ``adapterDefaults`` / request context.
        """
        if signal is not None:
            signal.throw_if_aborted()
        defaults: dict[str, bool] = {}
        max_tokens = getattr(self._provider, "max_tokens", None)
        if max_tokens is not None:
            defaults["maxTokens"] = True
        context_window = getattr(self._provider, "max_context_tokens", None) or self._max_context_tokens
        return PreparedCall(
            config=config,
            adapter_defaults=defaults,
            context={"contextWindow": context_window},
            retry_policy=None,
            stream=self.stream,
        )

    async def stream(self, options: GenerateOptions) -> AsyncIterator[Any]:
        """Emit the raw streaming protocol for one request (dsh adapter)."""
        messages = [_to_openai_message(m) for m in options.messages]
        tools = [_to_openai_tool(t) for t in (options.tools or ())]
        request = LLMRequest(
            messages=messages,
            tools=tools or None,
            max_tokens=options.max_tokens,
            temperature=options.temperature,
        )

        self._index = 0
        text_buffer: list[str] = []
        reasoning_buffer: list[str] = []
        text_open = reasoning_open = False
        emitted: set[str] = set()  # tool call ids already block-emitted
        index = 0

        async for delta in self._provider.achat_stream(request):
            content = delta.content or ""
            reasoning = delta.reasoning_content or ""
            if content:
                if not text_open:
                    yield BlockStartChunk(index=index, block_type="text")
                    text_open = True
                text_buffer.append(content)
                yield TextDeltaChunk(index=index, text=content)
            if reasoning:
                if not reasoning_open:
                    yield BlockStartChunk(index=index, block_type="reasoning")
                    reasoning_open = True
                reasoning_buffer.append(reasoning)
                yield ReasoningDeltaChunk(index=index, text=reasoning)
            # Provider tool calls arrive as cumulative snapshots per chunk:
            # diff against what we already emitted and emit one block per call.
            for tc in delta.tool_calls or []:
                if tc.id and tc.id not in emitted:
                    emitted.add(tc.id)
                    arguments = json.dumps(tc.arguments, ensure_ascii=False)
                    yield BlockStartChunk(index=index, block_type="tool-call")
                    yield ToolCallDeltaChunk(
                        index=index, id=tc.id, name=tc.name, arguments_delta=arguments
                    )
                    yield BlockEndChunk(
                        index=index,
                        block=ToolCallBlock(id=tc.id, name=tc.name, arguments=arguments),
                    )
                    index += 1
            if delta.prompt_tokens or delta.completion_tokens:
                yield UsageChunk(
                    usage=TokenUsage(
                        input_tokens=delta.prompt_tokens,
                        output_tokens=delta.completion_tokens,
                    )
                )
            if delta.finish_reason:
                yield FinishChunk(reason=_map_finish(delta.finish_reason))

        # Close any open content blocks with the fully assembled block (the
        # normal-exhaustion path only: an exception mid-stream leaves the
        # blocks open and ``normalized_stream`` turns the failure into a
        # terminal finish the core handles).
        if text_open:
            yield BlockEndChunk(index=0, block=TextBlock(text="".join(text_buffer)))
        if reasoning_open:
            yield BlockEndChunk(index=0, block=ReasoningBlock(text="".join(reasoning_buffer)))


# ---------------------------------------------------------------------------
# Serialization (dsh Messages / ToolSchema → OpenAI chat format)
# ---------------------------------------------------------------------------


def _to_openai_message(message: Message) -> dict[str, Any]:
    """Convert one dsh message to OpenAI chat dict (the provider's wire format)."""
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
            text = "".join(
                b.text for b in block.content if isinstance(b, TextBlock)
            )
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


__all__ = ["JavisLLMAdapter", "_to_openai_message", "_to_openai_tool"]
