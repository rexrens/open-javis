"""MockLLM: a scripted model provider for the demo (dsh adapter stand-in).

Implements the :class:`~dsh_harness.llm.LLM` seam exactly like a real adapter:
``prepare_call`` resolves exact-model adapter defaults (``adapterDefaults``
into the request header, ``contextWindow`` into the request context), and
``stream`` emits the raw streaming protocol — ``block-start`` /
``*-delta`` / ``block-end`` / ``usage`` / ``finish`` chunks — one response
per model call.

The *script* is a list of :class:`MockResponse`, consumed one per
``stream()`` call (the retry scenario's first response is an ``error``
finish that the ``agent/request-error`` waterfall retries). The optional
``on_tool_call(name, arguments)`` hook fires when a tool-call block is about
to be emitted — the steer scenario uses it to inject steering input
deterministically, mid-turn.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from .contracts import (
    AbortSignal,
    LlmCallConfig,
    LlmError,
    LlmFailure,
    MaxTokensFinish,
    ToolCallBlock,
)
from .llm import PreparedCall, chunk_response


@dataclass
class MockResponse:
    """One scripted model response (the unit of a single ``stream()`` call)."""

    text: str | None = None
    reasoning: str | None = None
    tool_calls: list[ToolCallBlock] = field(default_factory=list)
    usage: tuple[int, int] = (32, 16)
    #: When set, this response is a failure (terminal ``error`` finish).
    failure: LlmFailure | None = None
    #: When set, the response is a ``max-tokens`` finish (truncated text).
    max_tokens: bool = False
    #: Per-delta pacing; 0 keeps the stream synchronous.
    delay: float = 0.0


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> ToolCallBlock:
    return ToolCallBlock(id=call_id, name=name, arguments=json.dumps(arguments, ensure_ascii=False))


class MockLLM:
    """A scripted adapter over the LLM contract (no SDK at the seam)."""

    def __init__(self, script: list[MockResponse], model: str = "mock-mini") -> None:
        self._script = list(script)
        self._model = model
        self.call_count = 0
        #: Scenario hook: ``on_tool_call(name, arguments)`` before a call is emitted.
        self.on_tool_call: Callable[[str, Any], Any] | None = None
        #: Scenario hook: ``on_call(request)`` at the start of every stream.
        self.on_call: Callable[[Any], Any] | None = None

    # -- LLM service surface --------------------------------------------------

    def prepare_call(self, config: LlmCallConfig, signal: AbortSignal | None = None) -> PreparedCall:
        """Exact-model adapter resolution: defaults for ``mock-mini``.

        The adapter owns ``maxTokens`` for this model (4096) and advertises a
        context window — both surface as ``adapterDefaults`` / request
        context in the loop's request-header logging.
        """
        if signal is not None:
            signal.throw_if_aborted()
        resolved = config
        defaults: dict[str, bool] = {}
        if config.model.startswith("mock-mini") and config.max_tokens is None:
            resolved = LlmCallConfig(
                provider=config.provider,
                model=config.model,
                reasoning_effort=config.reasoning_effort,
                temperature=config.temperature,
                max_tokens=4096,
                stop=config.stop,
            )
            defaults["maxTokens"] = True
        return PreparedCall(
            config=resolved,
            adapter_defaults=defaults,
            context={"contextWindow": 8192},
            retry_policy={"retries": 1, "codes": ["TRANSIENT"]},
            stream=self.stream,  # the adapter binds its own stream for this registration
        )

    async def stream(self, options: Any) -> AsyncIterator[Any]:
        self.call_count += 1
        if self.on_call is not None:
            self.on_call(options)
        response = self._next_response()
        if response.failure is not None:
            # A provider-level failure mid-stream: the adapter throws; the
            # LLM runtime normalizes it to a terminal ``error`` finish.
            from .contracts import StopFinish

            del StopFinish
            # Stream a partial block first so interruption semantics are real.
            from .contracts import BlockStartChunk, TextDeltaChunk

            yield BlockStartChunk(index=0, block_type="text")
            yield TextDeltaChunk(index=0, text="…")
            raise LlmError(response.failure.message, response.failure.code, response.failure)

        chunks = chunk_response(
            text=response.text,
            reasoning=response.reasoning,
            tool_calls=response.tool_calls,
            usage=_usage(response),
            finish=_finish(response),
        )
        for chunk in chunks:
            # Fire the tool-call hook once per call, before its chunks.
            if self.on_tool_call is not None and chunk.type == "block-start" and chunk.block_type == "tool-call":
                block = self._tool_call_at(chunks, chunk.index)
                if block is not None:
                    args = json.loads(block.arguments) if block.arguments else {}
                    self.on_tool_call(block.name, args)
            if response.delay > 0:
                await asyncio.sleep(response.delay)
            yield chunk

    def _next_response(self) -> MockResponse:
        if self._script:
            return self._script.pop(0)
        # Script exhausted: a polite default keeps follow-ups well-formed.
        return MockResponse(text="(mock: script exhausted — nothing more to say)", usage=(8, 8))

    @staticmethod
    def _tool_call_at(chunks: list[Any], index: int) -> ToolCallBlock | None:
        for chunk in chunks:
            if chunk.type == "block-end" and chunk.index == index:
                return chunk.block
        return None


def _usage(response: MockResponse) -> Any:
    from .contracts import TokenUsage

    return TokenUsage(input_tokens=response.usage[0], output_tokens=response.usage[1])


def _finish(response: MockResponse) -> Any:
    if response.max_tokens:
        return MaxTokensFinish()
    if response.tool_calls:
        from .contracts import ToolCallsFinish

        return ToolCallsFinish()
    from .contracts import StopFinish

    return StopFinish()


# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------


def scenario_script(name: str) -> list[MockResponse]:
    """The four built-in demo scenarios (see ``demo/cli.py --scenario``)."""
    if name == "text":
        return [
            MockResponse(
                reasoning="2 + 2 is basic arithmetic; the answer is 4.",
                text="2 + 2 = 4.",
                usage=(24, 18),
            )
        ]
    if name == "tools":
        # Step 1: an exclusive barrier (set_note) + a parallel pair (weather×2).
        # Step 2: the summary, grounded in the tool results.
        return [
            MockResponse(
                tool_calls=[
                    _call("call_set_note_1", "set_note", {"text": "user asked for a weather comparison"}),
                    _call("call_weather_paris", "weather", {"city": "Paris"}),
                    _call("call_weather_tokyo", "weather", {"city": "Tokyo"}),
                ],
                usage=(48, 22),
            ),
            MockResponse(
                text="Paris is 18°C (light rain) and Tokyo is 24°C (sunny) — "
                "bring an umbrella for Paris.",
                usage=(64, 30),
            ),
        ]
    if name == "retry":
        # Step 1, attempt 1: a TRANSIENT provider failure (the demo
        # middleware's agent/request-error waterfall retries exactly once).
        # Step 1, attempt 2: the same step succeeds.
        return [
            MockResponse(
                failure=LlmFailure(message="connection reset by peer", code="TRANSIENT", status=502),
                usage=(16, 0),
            ),
            MockResponse(
                text="Recovered after one transient provider failure — all good.",
                usage=(20, 14),
            ),
        ]
    if name == "steer":
        # Step 1: the model calls ``now``; the scenario driver hooks
        # MockLLM.on_tool_call to steer "also include Tokyo's weather" into
        # the next-step inbox. Step 2: the model sees the steering.
        return [
            MockResponse(
                tool_calls=[_call("call_now_1", "now", {})],
                usage=(12, 6),
            ),
            MockResponse(
                text="It is 2026-08-31T18:00:00Z, and (per your steering) "
                "Tokyo's weather is 24°C sunny.",
                usage=(40, 26),
            ),
        ]
    raise ValueError(f"unknown scenario {name!r} (expected one of: text, tools, retry, steer)")


def steer_hook(agent: Any) -> Callable[[str, Any], Any]:
    """Build the steer scenario's ``on_tool_call`` hook for ``agent``.

    Fires once, when the mock is about to request the ``now`` tool: the
    steering message lands in the next-step inbox *before* the step's tool
    calls execute, so the next step's pre-step claims it deterministically.
    """
    from .contracts import UserMessage

    fired = False

    def on_tool_call(name: str, _arguments: Any) -> None:
        nonlocal fired
        if fired or name != "now":
            return
        fired = True
        agent.steer(UserMessage.from_text("also include Tokyo's weather in your answer"))

    return on_tool_call


__all__ = ["MockLLM", "MockResponse", "scenario_script", "steer_hook"]
