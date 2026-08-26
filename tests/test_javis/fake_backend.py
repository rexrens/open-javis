"""Minimal test engine implementing the ``AgentEngine`` contract.

Replaces the deleted MockAgent / FakeBackend: keeps the keyword routing so
end-to-end tests can exercise every render path (text deltas, tool calls,
errors) deterministically, without any model backend or network. Owns its
conversation history and usage like a real engine.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from javis.contracts.engine import AgentEngine
from javis.contracts.messages import ConversationMessage, TextBlock
from javis.contracts.types import (
    AgentError,
    AgentEvent,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.contracts.usage import UsageSnapshot


def _prompt_text(prompt: str | ConversationMessage) -> str:
    if isinstance(prompt, ConversationMessage):
        return prompt.text
    return prompt or ""


class FakeEngine(AgentEngine):
    """Canned test engine dispatching on prompt keywords (first match wins):

    - contains "error"  → emit ``AgentError`` and stop
    - contains "tool"   → emit a fake ``echo`` tool call then finish
    - otherwise         → echo the prompt back as a normal turn
    """

    model = "test-model"

    def __init__(self) -> None:
        self._messages: list[ConversationMessage] = []
        self._usage = UsageSnapshot()
        self._system_prompt = "fake system prompt"
        self._max_turns: int | None = None
        self._tool_metadata: dict[str, Any] = {}
        self._effort: str | None = None
        self.history_calls = 0

    @property
    def messages(self) -> list[ConversationMessage]:
        return list(self._messages)

    @property
    def total_usage(self) -> UsageSnapshot:
        return self._usage

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def max_turns(self) -> int | None:
        return self._max_turns

    @property
    def tool_metadata(self) -> dict[str, Any]:
        return self._tool_metadata

    def set_system_prompt(self, prompt: str) -> None:
        self._system_prompt = prompt

    def set_model(self, model: str) -> None:
        self.model = model

    def set_effort(self, effort: str | None) -> None:
        self._effort = effort

    def set_max_turns(self, max_turns: int | None) -> None:
        self._max_turns = None if max_turns is None else max(1, int(max_turns))

    def clear(self) -> None:
        self._messages.clear()
        self._usage = UsageSnapshot()

    def load_messages(self, messages: list[ConversationMessage]) -> None:
        self._messages = list(messages)

    async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[AgentEvent]:
        text = _prompt_text(prompt).strip()
        lower = text.lower()

        user_message = (
            prompt
            if isinstance(prompt, ConversationMessage)
            else ConversationMessage.from_user_text(prompt)
        )
        self._messages.append(user_message)

        if "error" in lower:
            yield AgentError(message=f"fake error triggered by prompt: {text!r}", recoverable=True)
            return

        if "tool" in lower:
            yield AgentTextDelta(text=f"I'll use the echo tool to repeat: {text!r}\n\n")
            yield AgentToolCallStart(tool_name="echo", tool_input={"text": text})
            yield AgentToolCallResult(
                tool_name="echo",
                output=json.dumps({"echoed": text}, ensure_ascii=False),
            )
            final = "The echo tool completed successfully.\n"
        else:
            final = f"fake reply to: {text}\n"

        self._messages.append(ConversationMessage(role="assistant", content=[TextBlock(text=final)]))
        yield AgentTextDelta(text=final)
        self._usage = UsageSnapshot(
            input_tokens=self._usage.input_tokens + len(text.split()),
            output_tokens=self._usage.output_tokens + len(final.split()),
        )
        yield AgentTurnEnd(text=final)
