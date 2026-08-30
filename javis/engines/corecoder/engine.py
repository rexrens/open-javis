"""CoreCoder engine — the single javis-side engine object for corecoder.

``CoreCoderEngine`` implements the ``AgentEngine`` contract: it owns the
conversation history (``ConversationMessage`` mirror, the authority for
session persistence), accumulates usage, and yields ``AgentEvent`` streams
per turn. Internally it drives ``corecoder.Agent`` (the pure chat/achat
loop) through a producer-consumer queue, bridging its callbacks to the
event stream.

This module replaces the old three-piece seam (``QueryEngine`` shell +
``AgentBackend`` protocol + ``CoreCoderBackend`` adapter); future engine
implementations implement ``AgentEngine`` directly (e.g. via the plugin
system).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from javis.contracts.engine import AgentEngine
from javis.contracts.messages import ConversationMessage, ImageBlock, TextBlock, ToolResultBlock
from javis.contracts.types import (
    AgentError,
    AgentEvent,
    AgentReasoningDelta,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.contracts.usage import UsageSnapshot

_IMAGE_PLACEHOLDER = "[image omitted: engine does not process images]"


def _user_text(content: list[Any]) -> str:
    """Join a user message's text blocks, substituting images with a placeholder."""
    parts = []
    for block in content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ImageBlock):
            parts.append(_IMAGE_PLACEHOLDER)
    return "".join(parts).strip()


def _to_corecoder_messages(messages: list[ConversationMessage]) -> list[dict[str, Any]]:
    """Convert javis conversation history into OpenAI-style message dicts.

    Tool results live in ``user`` messages in javis; corecoder expects them
    as standalone ``tool`` messages with ``tool_call_id``.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "user":
            tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]
            text = _user_text(msg.content)
            if text:
                out.append({"role": "user", "content": text})
            for tr in tool_results:
                out.append({"role": "tool", "tool_call_id": tr.tool_use_id, "content": tr.content})
        elif msg.role == "assistant":
            assistant: dict[str, Any] = {"role": "assistant", "content": msg.text or None}
            if msg.tool_uses:
                assistant["tool_calls"] = [
                    {
                        "id": tu.id,
                        "type": "function",
                        "function": {
                            "name": tu.name,
                            "arguments": json.dumps(tu.input),
                        },
                    }
                    for tu in msg.tool_uses
                ]
            out.append(assistant)
    return out


class CoreCoderEngine(AgentEngine):
    """javis-side engine over a ``corecoder.Agent``.

    Conversation history (``ConversationMessage``) is authoritative here and
    is mirrored into the agent's internal dict history on ``load_messages`` /
    ``clear``; per-turn the user prompt is handed to ``agent.achat`` whose
    callbacks are bridged to the ``AgentEvent`` stream.
    """

    def __init__(
        self,
        agent: Any,
        *,
        model: str,
        system_prompt: str = "",
        cwd: str | Path,
        max_turns: int | None = None,
        tool_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._agent = agent
        self._messages: list[ConversationMessage] = []
        self._model = model
        self._system_prompt = system_prompt
        self._cwd = str(Path(cwd).resolve())
        self._max_turns = max_turns
        self._tool_metadata: dict[str, Any] = tool_metadata or {}
        self._effort: str | None = None
        self._usage = UsageSnapshot()
        if system_prompt:
            agent.set_system_prompt(system_prompt)
        if max_turns is not None:
            agent.max_rounds = max(1, int(max_turns))

    @staticmethod
    def build(
        *,
        model: str,
        api_key: str,
        base_url: str,
        max_tokens: int | None = None,
        system_prompt: str = "",
        cwd: str | Path,
        max_turns: int | None = None,
        tool_metadata: dict[str, Any] | None = None,
    ) -> CoreCoderEngine:
        """Build the engine end-to-end: LLM provider + agent + engine shell.

        Replaces the old ``create_agent_backend`` / ``build_corecoder_backend``
        factory chain — config parsing stays in the caller (runtime), this
        method owns assembly.
        """
        from javis.engines.corecoder.agent import Agent
        from javis.engines.corecoder.llm import OpenAICompatProvider
        from javis.engines.corecoder.tools import all_tools

        provider_kwargs: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
        }
        if max_tokens is not None:
            provider_kwargs["max_tokens"] = max_tokens
        llm = OpenAICompatProvider(**provider_kwargs)
        agent = Agent(llm=llm, tools=all_tools())
        return CoreCoderEngine(
            agent,
            model=model,
            system_prompt=system_prompt,
            cwd=cwd,
            max_turns=max_turns,
            tool_metadata=tool_metadata,
        )

    @property
    def agent(self) -> Any:
        """The underlying corecoder.Agent (used by the host to inject hooks)."""
        return self._agent

    # --- properties -------------------------------------------------------

    @property
    def messages(self) -> list[ConversationMessage]:
        return list(self._messages)

    @property
    def max_turns(self) -> int | None:
        return self._max_turns

    @property
    def model(self) -> str:
        return self._model

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def tool_metadata(self) -> dict[str, Any]:
        return self._tool_metadata

    @property
    def total_usage(self) -> UsageSnapshot:
        return self._usage

    # --- setters (called by runtime / host on config changes) -------------

    def set_system_prompt(self, prompt: str) -> None:
        self._system_prompt = prompt
        self._agent.set_system_prompt(prompt)

    def set_model(self, model: str) -> None:
        self._model = model

    def set_effort(self, effort: str | None) -> None:
        self._effort = effort

    def set_max_turns(self, max_turns: int | None) -> None:
        self._max_turns = None if max_turns is None else max(1, int(max_turns))
        if self._max_turns is not None:
            self._agent.max_rounds = self._max_turns

    def set_permission_checker(
        self,
        checker: Any,
    ) -> None:
        """Optional AgentEngine hook: forward the host's permission hook to
        the inner agent loop (called before each tool execution)."""
        self._agent.permission_checker = checker

    def clear(self) -> None:
        self._messages.clear()
        self._usage = UsageSnapshot()
        self._agent.reset()

    def load_messages(self, messages: list[ConversationMessage]) -> None:
        self._messages = list(messages)
        self._agent.load_messages(_to_corecoder_messages(messages))

    # --- turn execution ---------------------------------------------------

    async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[AgentEvent]:
        """Run one turn: bridge ``agent.achat`` callbacks to the event stream.

        Producer-consumer over an asyncio.Queue: the achat task is the
        producer (native asyncio task, no thread bridge); this generator is
        the consumer. Cancellation of the consumer cancels the producer.
        """
        user_message = (
            prompt
            if isinstance(prompt, ConversationMessage)
            else ConversationMessage.from_user_text(prompt)
        )
        self._messages.append(user_message)
        prompt_text = _user_text(user_message.content)

        llm = self._agent.llm
        prompt_before = getattr(llm, "total_prompt_tokens", 0)
        completion_before = getattr(llm, "total_completion_tokens", 0)

        queue: asyncio.Queue[tuple[Any, ...]] = asyncio.Queue()

        def emit(item: tuple[Any, ...]) -> None:
            queue.put_nowait(item)

        async def producer() -> None:
            try:
                final = await self._agent.achat(
                    prompt_text,
                    on_token=lambda t: emit(("delta", t)),
                    on_reasoning=lambda t: emit(("reasoning", t)),
                    on_tool=lambda name, args: emit(("tool_start", name, args)),
                    on_tool_result=lambda n, a, out, err: emit(("tool_result", n, a, out, err)),
                )
                emit(("done", final))
            except Exception as exc:  # noqa: BLE001 — forward any producer failure as an error event
                emit(("error", exc))

        task = asyncio.create_task(producer())
        accumulated_text = ""
        try:
            while True:
                kind, *payload = await queue.get()
                if kind == "done":
                    final_text = payload[0]
                    break
                if kind == "error":
                    yield AgentError(message=str(payload[0]), recoverable=True)
                    return
                if kind == "delta":
                    accumulated_text += payload[0]
                    yield AgentTextDelta(text=payload[0])
                elif kind == "reasoning":
                    yield AgentReasoningDelta(text=payload[0])
                elif kind == "tool_start":
                    yield AgentToolCallStart(tool_name=payload[0], tool_input=payload[1])
                elif kind == "tool_result":
                    yield AgentToolCallResult(
                        tool_name=payload[0],
                        output=payload[2],
                        is_error=payload[3],
                    )
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        final_text = final_text or accumulated_text
        self._append_assistant(final_text)

        input_tokens = max(0, getattr(llm, "total_prompt_tokens", 0) - prompt_before)
        output_tokens = max(0, getattr(llm, "total_completion_tokens", 0) - completion_before)
        turn_usage = UsageSnapshot(input_tokens=input_tokens, output_tokens=output_tokens)
        if input_tokens or output_tokens:
            self._usage = UsageSnapshot(
                input_tokens=self._usage.input_tokens + turn_usage.input_tokens,
                output_tokens=self._usage.output_tokens + turn_usage.output_tokens,
            )
        else:
            self._usage = UsageSnapshot(
                input_tokens=self._usage.input_tokens + len(user_message.text.split()),
                output_tokens=self._usage.output_tokens + len(final_text.split()),
            )
        yield AgentTurnEnd(text=final_text, usage=turn_usage)

    def _append_assistant(self, text: str) -> ConversationMessage:
        message = ConversationMessage(role="assistant", content=[TextBlock(text=text)])
        self._messages.append(message)
        return message


__all__ = ["CoreCoderEngine"]
