"""QueryEngine: owns conversation history and delegates turns to an ``AgentBackend``.

This is the javis engine shell (equivalent of openharness' ``QueryEngine``) —
it does not implement a tool loop, permissions, hooks, compaction or provider
plumbing itself; those live in the injected backend. It just:

1. Appends the user message to history.
2. Calls ``AgentBackend.run_turn`` and yields ``AgentEvent`` straight through.
3. On ``AgentTurnEnd``, appends the assistant message to history.

The ``AgentBackend`` is the only seam — swap in a real backend (e.g.
``CoreCoderBackend``) without touching this engine or the TUI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

from javis.core.messages import ConversationMessage, TextBlock
from javis.core.usage import UsageSnapshot

from javis.core.protocol import AgentBackend
from javis.core.types import (
    AgentContext,
    AgentError,
    AgentEvent,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)


class QueryEngine:
    """Owns conversation history; delegates turn execution to an ``AgentBackend``."""

    def __init__(
        self,
        agent_backend: AgentBackend,
        *,
        model: str,
        system_prompt: str,
        cwd: str | Path,
        max_turns: int | None = None,
        tool_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._agent = agent_backend
        self._messages: list[ConversationMessage] = []
        self._model = model
        self._system_prompt = system_prompt
        self._cwd = str(Path(cwd).resolve())
        self._max_turns = max_turns
        self._tool_metadata: dict[str, Any] = tool_metadata or {}
        self._effort: str | None = None
        self._usage = UsageSnapshot()

    # --- properties ---

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

    # --- setters (called by runtime / host on config changes) ---

    def set_system_prompt(self, prompt: str) -> None:
        self._system_prompt = prompt

    def set_model(self, model: str) -> None:
        self._model = model

    def set_effort(self, effort: str | None) -> None:
        self._effort = effort

    def set_max_turns(self, max_turns: int | None) -> None:
        self._max_turns = None if max_turns is None else max(1, int(max_turns))

    def clear(self) -> None:
        self._messages.clear()
        self._usage = UsageSnapshot()
        if hasattr(self._agent, "clear_history"):
            self._agent.clear_history()

    def load_messages(self, messages: list[ConversationMessage]) -> None:
        self._messages = list(messages)

    def has_pending_continuation(self) -> bool:
        return False

    # --- turn execution ---

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

    async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[AgentEvent]:
        user_message = (
            prompt
            if isinstance(prompt, ConversationMessage)
            else ConversationMessage.from_user_text(prompt)
        )
        self._messages.append(user_message)

        context = self._build_context()
        accumulated_text = ""

        async for event in self._agent.run_turn(prompt, context=context):
            if isinstance(event, AgentTextDelta):
                accumulated_text += event.text
            elif isinstance(event, AgentTurnEnd):
                final_text = event.text or accumulated_text
                self._append_assistant(final_text)
                if event.usage is not None:
                    self._usage = UsageSnapshot(
                        input_tokens=self._usage.input_tokens + event.usage.input_tokens,
                        output_tokens=self._usage.output_tokens + event.usage.output_tokens,
                    )
                else:
                    self._usage = UsageSnapshot(
                        input_tokens=self._usage.input_tokens + len(user_message.text.split()),
                        output_tokens=self._usage.output_tokens + len(final_text.split()),
                    )
            yield event

    async def continue_pending(self, *, max_turns: int | None = None) -> AsyncIterator[AgentEvent]:
        """Mock agent has no real tool loop to resume."""
        del max_turns
        yield AgentStatus(message="[mock] continue_pending: nothing to resume.")
        return


__all__ = ["QueryEngine"]
