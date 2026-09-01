"""Tool registry + the model-ordered tool-call scheduler.

Port of the ``@deepseek-ai/dsh-tools`` runtime surface and
``packages/core/agent-loop/src/tool-calls.ts`` (dsh):

- **``ToolRegistry``** — service ``"tools"``: register (reversibly, via
  ``ctx.effect``), schema export, ``execution_mode(name)`` lookup, and the
  event hooks ``tools/execute`` (waterfall), ``tools/post-execute``
  (waterfall), ``tools/result`` (emit).
- **``execute_tool_calls``** — schedules one assistant step's tool calls by
  their live concurrency mode: exclusive calls form barriers; parallel calls
  run in a bounded rolling pool and are reclassified before start. Results
  and additional context commit in **model order**; ``concludesTurn`` marks
  the turn complete; abort drains started calls and records synthetic error
  results for the ones never started (dsh ``TOOL_ABORTED_BEFORE_DISPATCH``).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from typing import Any

from .contracts import (
    TOOL_ABORTED_BEFORE_DISPATCH,
    AbortError,
    AbortSignal,
    Events,
    ExclusiveMode,
    ParallelMode,
    PostToolDecision,
    SessionEvents,
    TextBlock,
    ToolExecutionInput,
    ToolExecutionResult,
    ToolSchema,
    UserMessage,
)
from .session import Session, create_tool_result_message


class Tool:
    """A registered tool: schema + execution mode + body.

    The body may be sync or async; it receives one
    :class:`ToolExecutionInput` and returns a
    :class:`ToolExecutionResult` (or a plain string, which is wrapped).
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        parameters: dict[str, Any] | None = None,
        mode: str = "parallel",
        body: Callable[..., Any] | None = None,
    ) -> None:
        self.name = name
        self.schema = ToolSchema(name=name, description=description, parameters=dict(parameters or {}))
        self.mode = mode  # "exclusive" | "parallel"
        self.body = body

    def execute(self, exec_input: ToolExecutionInput) -> Any:
        """Invoke the body (sync or async); the scheduler awaits awaitables."""
        if self.body is None:
            return ToolExecutionResult.text(f"Error: tool {self.name!r} has no body", is_error=True)
        return self.body(exec_input)


class ToolRegistry:
    """The ``"tools"`` service: reversible registration + execution hooks."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._tools: dict[str, Tool] = {}
        self._modes: dict[str, str] = {}

    # -- registration (reversible: disposers run when the fiber unloads) ----

    def register(self, tool: Tool, mode: str | None = None) -> Callable[[], Any]:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")

        def setup() -> Callable[[], Any]:
            self._tools[tool.name] = tool
            self._modes[tool.name] = (mode or tool.mode).lower()

            def disposer() -> Any:
                self._tools.pop(tool.name, None)
                self._modes.pop(tool.name, None)
                return None

            return disposer

        # Cordis effect contract: ``execute`` runs at load time and its return
        # value is the teardown disposer.
        return self.ctx.effect(setup, f"tools.register({tool.name!r})")

    # -- queries -------------------------------------------------------------

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[ToolSchema]:
        return [tool.schema for tool in self._tools.values()]

    def execution_mode(self, name: str) -> ExclusiveMode | ParallelMode:
        if self._modes.get(name, "parallel") == "exclusive":
            return ExclusiveMode()
        return ParallelMode()


def _wrap_result(name: str, result: Any) -> ToolExecutionResult:
    if isinstance(result, ToolExecutionResult):
        return result
    if isinstance(result, str):
        return ToolExecutionResult.text(result)
    # JSON-able value → text block (the model only sees text in this demo)
    try:
        return ToolExecutionResult.text(json.dumps(result, ensure_ascii=False))
    except (TypeError, ValueError):
        return ToolExecutionResult.text(repr(result))


def parse_arguments(raw: str) -> Any:
    """Parse model arguments, preserving invalid JSON as text (dsh semantics)."""
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return raw


async def _execute_one(
    registry: ToolRegistry,
    ctx: Any,
    exec_input: ToolExecutionInput,
) -> ToolExecutionResult:
    """Run one call through ``tools/execute`` + ``tools/post-execute`` + ``tools/result``."""
    signal = exec_input.signal
    tool = registry.get(exec_input.name)
    if tool is None:
        return ToolExecutionResult.text(f"Error: unknown tool {exec_input.name!r}", is_error=True)

    # -- tools/execute waterfall (wrap/replace the tool body) ---------------
    def _default(payload: Any, _next: Any) -> Any:
        return tool.execute(payload)  # sync or awaitable; awaited below

    result = ctx.waterfall(Events.TOOLS_EXECUTE, exec_input, _default)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, ToolExecutionResult):
        result = _wrap_result(exec_input.name, result)
    signal.throw_if_aborted()

    # -- tools/post-execute waterfall (rewrite content / add context) --------
    # The innermost default receives every dispatch arg plus the inner next.
    def _post_default(_exec: Any, _result: Any, _next: Any) -> Any:
        return None

    post = ctx.waterfall(Events.TOOLS_POST_EXECUTE, exec_input, result, _post_default)
    if inspect.isawaitable(post):
        post = await post
    if isinstance(post, PostToolDecision):
        if post.content is not None:
            result.content = list(post.content)
        if post.additional_contexts:
            result.additional_contexts = tuple(post.additional_contexts)
    signal.throw_if_aborted()

    # -- tools/result emit (result committed) --------------------------------
    ctx.emit(Events.TOOLS_RESULT, exec_input, result)
    return result


async def execute_tool_calls(
    ctx: Any,
    session: Session,
    agent: Any,
    turn: int,
    step: int,
    tool_calls: list[Any],
    signal: AbortSignal,
    accept_context: Callable[[UserMessage], Any],
) -> bool:
    """Schedule one assistant step's tool calls (dsh ``executeToolCalls``).

    Returns ``concluded`` — whether any committed result carried
    ``concludesTurn``. Abort records synthetic error results for skipped
    calls so replay stays valid.
    """
    registry: ToolRegistry = ctx.get("tools")
    max_parallel = _max_parallel_tool_calls(ctx)

    planned = [
        {
            "block": block,
            "exec": ToolExecutionInput(
                call_id=block.id,
                name=block.name,
                arguments=parse_arguments(block.arguments),
                agent=agent,
                signal=signal,
            ),
        }
        for block in tool_calls
    ]

    next = 0
    concluded = False
    while next < len(planned):
        # Commit before classifying again so registry changes affect
        # unstarted calls (a registry flip creates a new barrier).
        first = planned[next]
        mode = registry.execution_mode(first["exec"].name)
        group = [first] if mode.kind == "exclusive" else planned[next:]
        consumed, group_concluded, aborted = await _run_group(
            ctx, session, turn, step, group, max_parallel, signal, accept_context
        )
        next += consumed
        concluded = concluded or group_concluded
        if aborted:
            for call in planned[next:]:
                _append_skipped_tool_call(session, turn, step, call["block"])
            break
    return concluded


def _max_parallel_tool_calls(ctx: Any) -> int:
    config = getattr(ctx.get("agentLoop"), "config", None) or ctx.get("agentLoop")
    value = getattr(config, "max_parallel_tool_calls", 4)
    return max(1, int(value))


async def _run_group(
    ctx: Any,
    session: Session,
    turn: int,
    step: int,
    group: list[dict[str, Any]],
    max_parallel: int,
    signal: AbortSignal,
    accept_context: Callable[[UserMessage], Any],
) -> tuple[int, bool, bool]:
    """One exclusive barrier or parallel pool; results commit in model order.

    Returns ``(consumed, concluded, aborted)``. On abort the pool drains its
    started calls, commits their results, and the caller synthesizes results
    for the calls that never started.
    """
    registry: ToolRegistry = ctx.get("tools")
    slots: list[ToolExecutionResult | None] = [None] * len(group)
    call_seqs: list[int] = [-1] * len(group)
    committed = 0
    started = 0
    concluded = False
    next_to_start = 0
    in_flight: dict[int, asyncio.Task] = {}

    def commit_ready() -> None:
        nonlocal committed, concluded
        # ``committed`` advances only across contiguous model-order slots.
        while committed < len(group) and slots[committed] is not None:
            call = group[committed]
            result = slots[committed]
            assert result is not None
            _append_tool_result(session, turn, step, call["block"], result, call_seqs[committed])
            for context in result.additional_contexts:
                accept_context(context)
            concluded = concluded or result.concludes_turn
            committed += 1

    async def start_call(index: int) -> None:
        nonlocal started
        call = group[index]
        call_seqs[index] = _append_tool_call(session, turn, step, call["block"])
        started += 1
        try:
            slots[index] = await _execute_one(registry, ctx, call["exec"])
        except AbortError:
            slots[index] = _aborted_result(call["exec"])
        except Exception as exc:  # noqa: BLE001 — tool errors are text for the model
            slots[index] = ToolExecutionResult.text(
                f"Error executing {call['exec'].name}: {exc}",
                is_error=True,
            )

    try:
        while next_to_start < len(group) or in_flight:
            if not signal.aborted:
                # Fill the bounded pool; reclassify before start so a later
                # exclusive tool becomes the next barrier.
                while next_to_start < len(group) and len(in_flight) < max_parallel:
                    if (
                        next_to_start > 0
                        and registry.execution_mode(group[next_to_start]["exec"].name).kind == "exclusive"
                    ):
                        break
                    in_flight[next_to_start] = asyncio.ensure_future(start_call(next_to_start))
                    next_to_start += 1
            commit_ready()
            if not in_flight:
                break
            done, _ = await asyncio.wait(in_flight.values(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                index = next(i for i, t in in_flight.items() if t is task)
                in_flight.pop(index)
            commit_ready()
    finally:
        for task in in_flight.values():
            task.cancel()
        await asyncio.gather(*in_flight.values(), return_exceptions=True)

    if signal.aborted:
        # Started calls committed above; every remaining model call in the
        # group then receives an ordered synthetic result (dsh semantics).
        for index in range(started, len(group)):
            _append_skipped_tool_call(session, turn, step, group[index]["block"])
        return len(group), concluded, True
    return started, concluded, False


def _aborted_result(exec_input: ToolExecutionInput) -> ToolExecutionResult:
    return ToolExecutionResult.text(
        f"Error: tool call {exec_input.name!r} aborted before completion",
        is_error=True,
    )


# -- session event appenders (dsh appendToolCall / appendToolResult) --------


def _append_tool_call(session: Session, turn: int, step: int, block: Any) -> int:
    event = session.append(
        SessionEvents.TOOL_CALL,
        {"turn": turn, "step": step, "callId": block.id, "name": block.name, "arguments": block.arguments},
    )
    return event.seq


def _append_tool_result(
    session: Session,
    turn: int,
    step: int,
    block: Any,
    result: ToolExecutionResult,
    call_seq: int,
) -> None:
    message = create_tool_result_message(block.id, result.content, result.is_error)
    data: dict[str, Any] = {
        "turn": turn,
        "step": step,
        "message": message,
        "concludesTurn": result.concludes_turn,
    }
    if result.error:
        data["error"] = result.error
    if result.meta is not None:
        data["meta"] = result.meta
    session.append(SessionEvents.TOOL_RESULT, data, sourceEventSeqs=[call_seq])


def _append_skipped_tool_call(session: Session, turn: int, step: int, block: Any) -> None:
    """Append the durable call/result pair for a model call skipped after cancellation."""
    call_seq = _append_tool_call(session, turn, step, block)
    message = create_tool_result_message(
        block.id,
        [TextBlock("Error: tool call aborted before dispatch")],
        True,
    )
    session.append(
        SessionEvents.TOOL_RESULT,
        {
            "turn": turn,
            "step": step,
            "message": message,
            "error": {
                "message": "tool call aborted before dispatch",
                "code": TOOL_ABORTED_BEFORE_DISPATCH,
            },
        },
        sourceEventSeqs=[call_seq],
    )


__all__ = ["Tool", "ToolRegistry", "execute_tool_calls", "parse_arguments"]
