"""LLM service contract + stream assembly.

Port of the ``@deepseek-ai/dsh-llm`` runtime surface that the agent loop
consumes:

- **``LLM``** (dsh ``LlmRuntime``) — ``prepare_call(config)`` for
  exact-model adapter resolution, ``stream(options)`` for the raw streaming
  protocol. Adapters may throw; :func:`normalized_stream` turns any
  producer failure into a terminal ``error``/``aborted`` finish so consumers
  always see a well-formed stream.
- **``BlockAssembler``** (dsh ``BlockAssembler``) — folds
  :class:`~javis.dsh.contracts.StreamChunk` deltas into assembled content
  blocks, usage, and the terminal finish reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .contracts import (
    AbortError,
    AbortSignal,
    BlockEndChunk,
    BlockStartChunk,
    ErrorFinish,
    FinishChunk,
    FinishReason,
    GenerateOptions,
    LlmCallConfig,
    LlmError,
    LlmFailure,
    ReasoningDeltaChunk,
    StopFinish,
    TextBlock,
    TextDeltaChunk,
    TokenUsage,
    ToolCallBlock,
    ToolCallDeltaChunk,
    ToolCallsFinish,
)

# ---------------------------------------------------------------------------
# Adapter / request types (dsh: llm/index.ts PreparedLlmCall, LlmConfigurableProvider)
# ---------------------------------------------------------------------------


@dataclass
class PreparedCall:
    """The adapter registration that resolved one request's exact-model defaults."""

    config: LlmCallConfig
    #: Which config fields were supplied by the adapter, not the caller
    #: (``{"reasoningEffort": True}`` etc.) — logged into the request header.
    adapter_defaults: dict[str, bool] = field(default_factory=dict)
    #: Adapter context (``{"contextWindow": int}``) when advertised.
    context: dict[str, Any] | None = None
    #: Optional retry policy (consumed by ``agent/request-error`` listeners).
    retry_policy: dict[str, Any] | None = None
    #: Adapter-bound stream for this exact-model registration; ``None`` lets
    #: the loop fall back to the provider's plain ``stream(options)``.
    stream: Callable[[GenerateOptions], AsyncIterator[Any]] | None = None


@runtime_checkable
class LLM(Protocol):
    """The model service. Implementations must be SDK-free at this seam."""

    def prepare_call(
        self, config: LlmCallConfig, signal: AbortSignal | None = None
    ) -> PreparedCall:
        """Resolve exact-model adapter defaults for ``config``."""
        ...

    def stream(self, options: GenerateOptions) -> AsyncIterator[Any]:
        """Emit the raw streaming protocol for one request (a coroutine object)."""
        ...


# ---------------------------------------------------------------------------
# Stream normalization
# ---------------------------------------------------------------------------


async def normalized_stream(
    stream: Any,
    options: GenerateOptions,
    signal: AbortSignal | None = None,
) -> AsyncIterator[Any]:
    """Wrap a provider stream so failures become terminal finish chunks.

    dsh: ``An adapter implementation may throw, but LlmRuntime.stream()
    normalizes that failure to a terminal error or aborted finish before
    exposing it to consumers.``
    """
    try:
        async for chunk in stream:
            if signal is not None:
                signal.throw_if_aborted()
            yield chunk
    except AbortError:
        if signal is not None and signal.aborted:
            yield FinishChunk(reason=_aborted_finish(signal))
    except Exception as exc:  # noqa: BLE001 — normalized to a terminal finish below
        if signal is not None and signal.aborted:
            yield FinishChunk(reason=_aborted_finish(signal))
            return
        if isinstance(exc, LlmError):
            failure: LlmFailure = exc.failure
        else:
            failure = LlmFailure(message=str(exc), code="UNKNOWN")
        yield FinishChunk(reason=ErrorFinish(failure=failure))


def _aborted_finish(signal: AbortSignal) -> FinishReason:
    from .contracts import AbortedFinish

    cause = signal.reason
    return AbortedFinish(
        failure=LlmFailure(
            message=str(cause.detail or cause.kind),
            code="ABORTED",
        )
    )


# ---------------------------------------------------------------------------
# BlockAssembler
# ---------------------------------------------------------------------------


class BlockAssembler:
    """Fold stream chunks into assembled blocks + usage + terminal finish.

    Block indexes correlate interleaved deltas; ``block-end`` carries the
    assembled block (dsh ``BlockAssembler``). Adapters emit usage before the
    terminal finish and nothing afterward.
    """

    def __init__(self) -> None:
        self._blocks: list[Any] = []
        self._open: dict[int, dict[str, Any]] = {}
        self._index_by_type: dict[str, list[int]] = {}
        self.usage: TokenUsage | None = None
        self.finish: FinishReason | None = None

    @property
    def blocks(self) -> list[Any]:
        return list(self._blocks)

    def push(self, chunk: Any) -> None:
        kind = chunk.type
        if kind == "block-start":
            state = {"block_type": chunk.block_type, "text": "", "id": "", "name": None, "args": ""}
            self._open[chunk.index] = state
            self._index_by_type.setdefault(chunk.block_type, []).append(chunk.index)
        elif kind == "text-delta" or kind == "reasoning-delta":
            self._open[chunk.index]["text"] += chunk.text
        elif kind == "tool-call-delta":
            state = self._open[chunk.index]
            if chunk.id:
                state["id"] = chunk.id
            if chunk.name:
                state["name"] = chunk.name
            state["args"] += chunk.arguments_delta
        elif kind == "block-end":
            self._open.pop(chunk.index, None)
            self._blocks.append(chunk.block)
        elif kind == "usage":
            self.usage = chunk.usage
        elif kind == "finish":
            self.finish = chunk.reason
        else:  # unknown chunk kinds are ignored (merge-extensible protocol)
            return

    def _state_for(self, block_type: str) -> dict[str, Any] | None:
        indexes = self._index_by_type.get(block_type, [])
        for index in indexes:
            if index in self._open:
                return self._open[index]
        return None

    def interrupted_blocks(self) -> list[Any]:
        """The partially-assembled blocks, materialized (dsh ``interruptedBlocks``)."""
        out: list[Any] = []
        for index, state in self._open.items():
            block_type = state["block_type"]
            if block_type == "text" and state["text"]:
                out.append(TextBlock(text=state["text"]))
            elif block_type == "reasoning" and state["text"]:
                from .contracts import ReasoningBlock

                out.append(ReasoningBlock(text=state["text"]))
            elif block_type == "tool-call" and state["name"]:
                    out.append(
                        ToolCallBlock(id=state["id"] or f"call_{index}", name=state["name"], arguments=state["args"])
                    )
        return out

    def _replayable(self) -> list[Any]:
        return self._blocks


def assemble_finish(blocks: list[Any], usage: TokenUsage | None = None) -> FinishReason:
    """Infer a finish reason when an adapter omits one (stop vs tool-calls)."""
    if any(isinstance(block, ToolCallBlock) for block in blocks):
        return ToolCallsFinish()
    del usage
    return StopFinish()


# Convenience for adapters/tests: build a full chunk sequence for one response.
def chunk_response(
    *,
    text: str | None = None,
    reasoning: str | None = None,
    tool_calls: list[ToolCallBlock] | None = None,
    usage: TokenUsage | None = None,
    finish: FinishReason | None = None,
) -> list[Any]:
    chunks: list[Any] = []
    index = 0
    if reasoning is not None:
        chunks += [
            BlockStartChunk(index=index, block_type="reasoning"),
            ReasoningDeltaChunk(index=index, text=reasoning),
            BlockEndChunk(index=index, block=_reasoning_block(reasoning)),
        ]
        index += 1
    if text is not None:
        chunks += [
            BlockStartChunk(index=index, block_type="text"),
            TextDeltaChunk(index=index, text=text),
            BlockEndChunk(index=index, block=TextBlock(text=text)),
        ]
        index += 1
    for call in tool_calls or []:
        chunks += [
            BlockStartChunk(index=index, block_type="tool-call"),
            ToolCallDeltaChunk(index=index, id=call.id, name=call.name, arguments_delta=call.arguments),
            BlockEndChunk(index=index, block=call),
        ]
        index += 1
    if usage is not None:
        from .contracts import UsageChunk

        chunks.append(UsageChunk(usage=usage))
    if finish is None:
        blocks: list[Any] = []
        if reasoning is not None:
            blocks.append(_reasoning_block(reasoning))
        if text is not None:
            blocks.append(TextBlock(text=text))
        blocks.extend(tool_calls or [])
        finish = assemble_finish(blocks, usage)
    chunks.append(FinishChunk(reason=finish))
    return chunks


def _reasoning_block(text: str) -> Any:
    from .contracts import ReasoningBlock

    return ReasoningBlock(text=text)


__all__ = [
    "LLM",
    "BlockAssembler",
    "PreparedCall",
    "assemble_finish",
    "chunk_response",
    "normalized_stream",
]
