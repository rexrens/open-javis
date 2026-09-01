"""Standalone harness — a second ``AgentEngine`` implementation.

This is the "write your own harness" story made concrete: it implements the
``javis.contracts`` seam (``AgentEngine``) with *its own* agent loop, *its
own* provider abstraction (``providers.ChatProvider``), *its own* tool
execution and permission path. It imports nothing from ``javis.harness``:
the plugin composition root (``harness_plugin.py``) wires it to javis via the
built-in services (``config`` / ``tools`` / ``commands`` / ``host``), and the
host drives it exactly like the built-in ``HarnessEngine``.

Turn loop::

    user message
      └─ provider.complete(history, tool schemas)  → text/reasoning deltas
           └─ tool calls?
                ├─ no  → AgentTurnEnd
                └─ yes → per call:
                            permission_checker? → deny → error result
                            execute(tool, args)  → AgentToolCallResult
                         append tool results to history → next round
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from providers import ChatProvider, ProviderResult, ToolCallDraft

from javis.contracts.engine import AgentEngine
from javis.contracts.messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from javis.contracts.types import (
    AgentError,
    AgentEvent,
    AgentReasoningDelta,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.contracts.usage import UsageSnapshot


@dataclass(frozen=True)
class _ProviderDone:
    """Internal sentinel: one provider round finished (result or None on error)."""

    result: ProviderResult | None


class HarnessEngine(AgentEngine):
    """A minimal standalone engine: own provider, own loop, own tool execution."""

    def __init__(
        self,
        *,
        model: str,
        provider: ChatProvider,
        tools: list[Any] | None = None,
        system_prompt: str = "",
        cwd: str | Path = "",
        workspace: str | Path = "",
        session_id: str = "",
        max_turns: int | None = None,
        tool_metadata: dict[str, Any] | None = None,
        permission_checker: Any = None,
    ) -> None:
        self._model = model
        self._provider = provider
        self._tools = list(tools or [])
        self._tools_by_name = {tool.name: tool for tool in self._tools}
        self._system_prompt = system_prompt
        self._cwd = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
        self._workspace = str(Path(workspace).expanduser().resolve()) if workspace else self._cwd
        self._session_id = session_id
        self._max_turns = None if max_turns is None else max(1, int(max_turns))
        self._tool_metadata = dict(tool_metadata or {})
        self._messages: list[ConversationMessage] = []
        self._usage = UsageSnapshot()
        self._permission_checker = permission_checker

    # --- identity (used by the /harness status command) -------------------

    @property
    def harness_name(self) -> str:
        return f"plugin-harness ({type(self._provider).__name__} @ {self._model})"

    @property
    def tools(self) -> list[str]:
        return [tool.name for tool in self._tools]

    @property
    def session_id(self) -> str:
        return self._session_id

    # --- AgentEngine properties -------------------------------------------

    @property
    def messages(self) -> list[ConversationMessage]:
        return list(self._messages)

    @property
    def total_usage(self) -> UsageSnapshot:
        return self._usage

    @property
    def model(self) -> str:
        return self._model

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def max_turns(self) -> int | None:
        return self._max_turns

    @property
    def tool_metadata(self) -> dict[str, Any]:
        return self._tool_metadata

    # --- setters (called by the runtime / host on config changes) ---------

    def set_system_prompt(self, prompt: str) -> None:
        self._system_prompt = prompt

    def set_model(self, model: str) -> None:
        self._model = model

    def set_effort(self, effort: str | None) -> None:
        del effort  # this harness leaves effort to the provider

    def set_max_turns(self, max_turns: int | None) -> None:
        self._max_turns = None if max_turns is None else max(1, int(max_turns))

    def set_permission_checker(self, checker: Any) -> None:
        """Optional AgentEngine hook: receive the host's async permission
        callback (``checker(tool_name, arguments) -> "allow" | deny-reason``)
        and consult it before executing every tool."""
        self._permission_checker = checker

    def clear(self) -> None:
        self._messages.clear()
        self._usage = UsageSnapshot()

    def load_messages(self, messages: list[ConversationMessage]) -> None:
        self._messages = list(messages)

    # --- turn execution ---------------------------------------------------

    async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[AgentEvent]:
        """Run one user turn through the provider loop, executing tools."""
        user_message = (
            prompt
            if isinstance(prompt, ConversationMessage)
            else ConversationMessage.from_user_text(prompt)
        )
        self._messages.append(user_message)

        round_no = 0
        turn_usage = UsageSnapshot()
        while True:
            if self._max_turns is not None and round_no >= self._max_turns:
                yield AgentStatus(message=f"[harness] reached max_turns={self._max_turns}")
                yield AgentTurnEnd(text="", usage=turn_usage)
                return
            round_no += 1

            schemas = [tool.schema() for tool in self._tools]
            result: ProviderResult | None = None
            async for event in self._provider_stream(self._serialize(), schemas):
                if isinstance(event, _ProviderDone):
                    result = event.result
                    break
                yield event
            if result is None:
                return  # an AgentError was already yielded

            if result.usage:
                input_tokens = result.usage.get("input_tokens", 0)
                output_tokens = result.usage.get("output_tokens", 0)
                turn_usage = UsageSnapshot(
                    input_tokens=turn_usage.input_tokens + input_tokens,
                    output_tokens=turn_usage.output_tokens + output_tokens,
                )
                self._usage = UsageSnapshot(
                    input_tokens=self._usage.input_tokens + input_tokens,
                    output_tokens=self._usage.output_tokens + output_tokens,
                )

            assistant_blocks: list[Any] = [TextBlock(text=result.content)] if result.content else []
            assistant_blocks += [
                ToolUseBlock(id=call.id, name=call.name, input=call.arguments)
                for call in result.tool_calls
            ]
            self._messages.append(ConversationMessage(role="assistant", content=assistant_blocks))

            if not result.tool_calls:
                yield AgentTurnEnd(text=result.content, usage=turn_usage)
                return

            tool_results: list[ContentBlock] = []
            for call in result.tool_calls:
                yield AgentToolCallStart(tool_name=call.name, tool_input=call.arguments)
                output, is_error = await self._execute_tool(call)
                yield AgentToolCallResult(tool_name=call.name, output=output, is_error=is_error)
                tool_results.append(
                    ToolResultBlock(tool_use_id=call.id, content=output, is_error=is_error)
                )
            # one user message carrying every tool result of this round
            self._messages.append(ConversationMessage(role="user", content=tool_results))

    async def _provider_stream(
        self,
        messages: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent | _ProviderDone]:
        """Run one provider call, bridging its callbacks to the event stream.

        Producer-consumer over an asyncio.Queue (same pattern as the built-in
        HarnessEngine): the provider task is the producer, this generator is
        the consumer, and cancellation of the consumer cancels the producer.
        """
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        async def producer() -> None:
            try:
                result = await self._provider.complete(
                    messages,
                    schemas,
                    on_text=lambda text: queue.put_nowait(("delta", text)),
                    on_reasoning=lambda text: queue.put_nowait(("reasoning", text)),
                )
                queue.put_nowait(("done", result))
            except BaseException as exc:  # noqa: BLE001 — surfaced as AgentError
                queue.put_nowait(("error", exc))

        task = asyncio.create_task(producer())
        error: BaseException | None = None
        result: ProviderResult | None = None
        try:
            while error is None and result is None:
                kind, payload = await queue.get()
                if kind == "delta":
                    yield AgentTextDelta(text=payload)
                elif kind == "reasoning":
                    yield AgentReasoningDelta(text=payload)
                elif kind == "done":
                    result = payload
                elif kind == "error":
                    error = payload
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if error is not None:
            yield AgentError(message=str(error), recoverable=True)
        yield _ProviderDone(result)

    async def _execute_tool(self, call: ToolCallDraft) -> tuple[str, bool]:
        """Run one tool call, returning ``(output_text, is_error)``."""
        if self._permission_checker is not None:
            decision = await self._permission_checker(call.name, call.arguments)
            if decision != "allow":
                return f"[permission denied: {decision}]", True
        tool = self._tools_by_name.get(call.name)
        if tool is None:
            return f"Error: unknown tool {call.name!r}", True
        try:
            inspect.signature(tool.execute).bind(**call.arguments)
        except TypeError as exc:
            return f"Error: bad arguments for {call.name}: {exc}", True
        try:
            return tool.execute(**call.arguments), False
        except Exception as exc:  # noqa: BLE001 — tool errors are text for the model
            return f"Error executing {call.name}: {exc}", True

    def _serialize(self) -> list[dict[str, Any]]:
        """Convert the conversation mirror to OpenAI-style message dicts."""
        out: list[dict[str, Any]] = []
        if self._system_prompt:
            out.append({"role": "system", "content": self._system_prompt})
        for message in self._messages:
            if message.role == "user":
                text = "".join(block.text for block in message.content if isinstance(block, TextBlock))
                tool_results = [
                    block for block in message.content if isinstance(block, ToolResultBlock)
                ]
                if text:
                    out.append({"role": "user", "content": text})
                for result in tool_results:
                    out.append(
                        {"role": "tool", "tool_call_id": result.tool_use_id, "content": result.content}
                    )
            else:
                entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
                if message.tool_uses:
                    entry["tool_calls"] = [
                        {
                            "id": use.id,
                            "type": "function",
                            "function": {"name": use.name, "arguments": json.dumps(use.input)},
                        }
                        for use in message.tool_uses
                    ]
                out.append(entry)
        return out


__all__ = ["HarnessEngine"]
