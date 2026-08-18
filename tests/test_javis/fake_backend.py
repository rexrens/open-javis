"""Minimal test backend that replaces the deleted MockAgent.

Keeps the keyword routing of the old MockAgent so end-to-end tests can still
exercise every render path (text deltas, tool calls, errors) deterministically,
without any model backend or network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from javis.core.protocol import AgentBackend
from javis.core.types import (
    AgentContext,
    AgentError,
    AgentEvent,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.messages import ConversationMessage


def _prompt_text(prompt: str | ConversationMessage) -> str:
    if isinstance(prompt, ConversationMessage):
        return prompt.text
    return prompt or ""


class FakeBackend(AgentBackend):
    """Canned test backend dispatching on prompt keywords (first match wins):

    - contains "error"  → emit ``AgentError`` and stop
    - contains "tool"   → emit a fake ``echo`` tool call then finish
    - otherwise         → echo the prompt back as a normal turn
    """

    model = "test-model"

    def __init__(self) -> None:
        self.history_calls = 0

    async def run_turn(
        self,
        prompt: str | ConversationMessage,
        *,
        context: AgentContext,
    ) -> AsyncIterator[AgentEvent]:
        text = _prompt_text(prompt).strip()
        lower = text.lower()

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
            yield AgentTurnEnd(text=final)
            return

        reply = f"fake reply to: {text}"
        yield AgentTextDelta(text=reply + "\n")
        yield AgentTurnEnd(text=reply + "\n")

    def load_history(self, messages) -> None:
        self.history_calls += 1
