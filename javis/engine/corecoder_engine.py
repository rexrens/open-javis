"""CoreCoder engine: a real agent engine backed by the CoreCoder loop.

Implements the same duck-typed contract that ``openharness.ui.runtime.handle_line``,
``refresh_runtime_client``, ``sync_app_state`` and ``ReactBackendHost`` expect from
``RuntimeBundle.engine`` — but instead of a mock, it drives the real CoreCoder
``Agent.chat`` loop.

The CoreCoder loop is synchronous, so the bridge is a worker thread + queue:
CoreCoder callbacks (``on_token`` / ``on_tool`` / ``on_tool_result``) emit
events into an ``asyncio.Queue`` via ``call_soon_threadsafe``, and the async
generator relays them as OpenHarness ``StreamEvent``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import (
    ConversationMessage,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
)
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)

from javis.corecoder.agent import Agent

_IMAGE_PLACEHOLDER = "[image omitted: CoreCoder engine does not process images]"


def _to_corecoder_messages(messages: list[ConversationMessage]) -> list[dict]:
    """Convert OpenHarness conversation history into CoreCoder (OpenAI) message dicts.

    Tool results live in ``user`` messages in OpenHarness; CoreCoder expects
    them as standalone ``tool`` messages with ``tool_call_id``.
    """
    out: list[dict] = []
    for msg in messages:
        if msg.role == "user":
            tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]
            text_parts = []
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ImageBlock):
                    text_parts.append(_IMAGE_PLACEHOLDER)
            text = "".join(text_parts).strip()
            if text:
                out.append({"role": "user", "content": text})
            for tr in tool_results:
                out.append({
                    "role": "tool",
                    "tool_call_id": tr.tool_use_id,
                    "content": tr.content,
                })
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


class CoreCoderEngine:
    """Adapter exposing a ``QueryEngine``-shaped surface over a CoreCoder ``Agent``."""

    def __init__(
        self,
        agent: Agent,
        *,
        model: str,
        system_prompt: str,
        cwd: str | Path,
        max_turns: int | None = None,
        tool_metadata: dict[str, Any] | None = None,
        api_client: Any | None = None,
    ) -> None:
        self._agent = agent
        self._messages: list[ConversationMessage] = []
        self._model = model
        self._system_prompt = system_prompt
        self._cwd = str(Path(cwd).resolve())
        self._max_turns = max_turns
        self._tool_metadata: dict[str, Any] = tool_metadata or {}
        self._api_client = api_client
        self._effort: str | None = None
        self._usage = UsageSnapshot()

        if system_prompt:
            agent._system = system_prompt
        if max_turns is not None:
            agent.max_rounds = max(1, int(max_turns))

    # --- properties (read by handle_line / sync_app_state / backend_host) ---

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

    @property
    def api_client(self) -> Any:
        return self._api_client

    # --- setters (called by refresh_runtime_client / sync_app_state / handle_line) ---

    def set_system_prompt(self, prompt: str) -> None:
        self._system_prompt = prompt
        self._agent._system = prompt

    def set_model(self, model: str) -> None:
        self._model = model
        self._agent.llm.model = model

    def set_effort(self, effort: str | None) -> None:
        self._effort = effort

    def set_api_client(self, api_client: Any) -> None:
        self._api_client = api_client

    def set_max_turns(self, max_turns: int | None) -> None:
        self._max_turns = None if max_turns is None else max(1, int(max_turns))
        if self._max_turns is not None:
            self._agent.max_rounds = self._max_turns

    def set_permission_checker(self, checker: Any) -> None:
        # CoreCoder tools run inside the agent loop without openharness's
        # permission layer; the BashTool keeps its own dangerous-command guard.
        return None

    def clear(self) -> None:
        self._messages.clear()
        self._agent.reset()
        self._usage = UsageSnapshot()

    def load_messages(self, messages: list[ConversationMessage]) -> None:
        self._messages = list(messages)
        self._agent.messages = _to_corecoder_messages(messages)

    def has_pending_continuation(self) -> bool:
        # CoreCoder runs a turn to completion in one call; nothing to resume.
        return False

    # --- async turn execution ---

    def _append_assistant(self, text: str) -> ConversationMessage:
        message = ConversationMessage(role="assistant", content=[TextBlock(text=text)])
        self._messages.append(message)
        return message

    async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[StreamEvent]:
        user_message = (
            prompt
            if isinstance(prompt, ConversationMessage)
            else ConversationMessage.from_user_text(prompt)
        )
        self._messages.append(user_message)

        loop = asyncio.get_running_loop()
        aqueue: asyncio.Queue[tuple] = asyncio.Queue()

        def _emit(item: tuple) -> None:
            loop.call_soon_threadsafe(aqueue.put_nowait, item)

        def run() -> None:
            try:
                result = self._agent.chat(
                    user_message.text,
                    on_token=lambda t: _emit(("delta", t)),
                    on_tool=lambda name, args: _emit(("tool_start", name, args)),
                    on_tool_result=lambda name, out, err: _emit(("tool_result", name, out, err)),
                )
                _emit(("done", result))
            except Exception as exc:
                _emit(("error", exc))

        llm = self._agent.llm
        prompt_tokens_before = getattr(llm, "total_prompt_tokens", 0)
        completion_before = getattr(llm, "total_completion_tokens", 0)

        task = asyncio.create_task(asyncio.to_thread(run))
        final_text: str | None = None
        try:
            while True:
                item = await aqueue.get()
                kind = item[0]
                if kind == "delta":
                    yield AssistantTextDelta(text=item[1])
                elif kind == "tool_start":
                    yield ToolExecutionStarted(tool_name=item[1], tool_input=item[2])
                elif kind == "tool_result":
                    yield ToolExecutionCompleted(
                        tool_name=item[1],
                        output=item[2],
                        is_error=item[3],
                    )
                elif kind == "done":
                    final_text = item[1]
                    break
                elif kind == "error":
                    yield ErrorEvent(message=str(item[1]), recoverable=True)
                    return
        finally:
            task.cancel()

        final_text = final_text if final_text is not None else "(no response)"
        assistant_msg = self._append_assistant(final_text)
        input_tokens = getattr(llm, "total_prompt_tokens", 0) - prompt_tokens_before
        output_tokens = getattr(llm, "total_completion_tokens", 0) - completion_before
        self._usage = UsageSnapshot(
            input_tokens=self._usage.input_tokens + max(0, input_tokens),
            output_tokens=self._usage.output_tokens + max(0, output_tokens),
        )
        yield AssistantTurnComplete(message=assistant_msg, usage=self._usage)

    async def continue_pending(self, *, max_turns: int | None = None) -> AsyncIterator[StreamEvent]:
        # CoreCoder has no interrupted tool loop to resume; emit a status and end.
        del max_turns
        yield StatusEvent(message="[corecoder] continue_pending: nothing to resume.")
        return


__all__ = ["CoreCoderEngine", "_to_corecoder_messages"]
