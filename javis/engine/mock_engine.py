"""Mock engine: drop-in replacement for ``QueryEngine`` backed by an ``AgentBackend``.

Implements the duck-typed contract that ``openharness.ui.runtime.handle_line``,
``refresh_runtime_client``, ``sync_app_state`` and ``ReactBackendHost`` expect
from ``RuntimeBundle.engine`` — without depending on a real model.

The translation is one-way: ``AgentEvent`` (framework-neutral) →
``StreamEvent`` (OpenHarness-specific). A real agent only needs to emit
``AgentEvent``; this engine handles the rest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)

from javis.engine.protocol import AgentBackend
from javis.engine.types import (
    AgentContext,
    AgentError,
    AgentEvent,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)


class MockEngine:
    """Adapter that exposes a ``QueryEngine``-shaped surface over an ``AgentBackend``."""

    def __init__(
        self,
        agent_backend: AgentBackend,
        *,
        model: str,
        system_prompt: str,
        cwd: str | Path,
        max_turns: int | None = None,
        tool_metadata: dict[str, Any] | None = None,
        api_client: Any | None = None,
    ) -> None:
        self._agent = agent_backend
        self._messages: list[ConversationMessage] = []
        self._model = model
        self._system_prompt = system_prompt
        self._cwd = str(Path(cwd).resolve())
        self._max_turns = max_turns
        self._tool_metadata: dict[str, Any] = tool_metadata or {}
        self._api_client = api_client
        self._effort: str | None = None
        self._usage = UsageSnapshot()

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

    def set_model(self, model: str) -> None:
        self._model = model

    def set_effort(self, effort: str | None) -> None:
        self._effort = effort

    def set_api_client(self, api_client: Any) -> None:
        self._api_client = api_client

    def set_max_turns(self, max_turns: int | None) -> None:
        self._max_turns = None if max_turns is None else max(1, int(max_turns))

    def set_permission_checker(self, checker: Any) -> None:
        # Mock agent doesn't enforce permissions through the engine; the
        # backend host's modal flow handles tool approvals independently.
        return None

    def clear(self) -> None:
        self._messages.clear()
        self._usage = UsageSnapshot()

    def load_messages(self, messages: list[ConversationMessage]) -> None:
        self._messages = list(messages)

    def has_pending_continuation(self) -> bool:
        return False

    # --- async turn execution ---

    def _build_context(self) -> AgentContext:
        return AgentContext(
            cwd=self._cwd,
            model=self._model,
            system_prompt=self._system_prompt,
            messages=list(self._messages),
            tool_metadata=self._tool_metadata,
        )

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

        context = self._build_context()
        accumulated_text = ""
        turn_final_text: str | None = None

        async for event in self._agent.run_turn(prompt, context=context):
            if isinstance(event, AgentTextDelta):
                accumulated_text += event.text
                yield AssistantTextDelta(text=event.text)
            elif isinstance(event, AgentToolCallStart):
                yield ToolExecutionStarted(tool_name=event.tool_name, tool_input=event.tool_input)
            elif isinstance(event, AgentToolCallResult):
                yield ToolExecutionCompleted(
                    tool_name=event.tool_name,
                    output=event.output,
                    is_error=event.is_error,
                    metadata=event.metadata,
                )
            elif isinstance(event, AgentStatus):
                yield StatusEvent(message=event.message)
            elif isinstance(event, AgentError):
                yield ErrorEvent(message=event.message, recoverable=event.recoverable)
                return
            elif isinstance(event, AgentTurnEnd):
                turn_final_text = event.text or accumulated_text

        final_text = turn_final_text if turn_final_text is not None else accumulated_text
        assistant_msg = self._append_assistant(final_text)
        # Mock usage so the UI's token counter renders plausibly.
        self._usage = UsageSnapshot(
            input_tokens=self._usage.input_tokens + len(user_message.text.split()),
            output_tokens=self._usage.output_tokens + len(final_text.split()),
        )
        yield AssistantTurnComplete(message=assistant_msg, usage=self._usage)

    async def continue_pending(self, *, max_turns: int | None = None) -> AsyncIterator[StreamEvent]:
        # Mock agent has no real tool loop to resume; emit a status and end.
        del max_turns
        yield StatusEvent(message="[mock] continue_pending: nothing to resume.")
        return


__all__ = ["MockEngine"]
