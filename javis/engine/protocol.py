"""Agent backend protocol — the seam where a real agent plugs in.

Implement this ``Protocol`` to wire a custom agent into the javis TUI.
``MockAgent`` is the reference implementation; replace it with a real one
without touching ``MockEngine`` or any TUI code.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from openharness.engine.messages import ConversationMessage

from javis.engine.types import AgentContext, AgentEvent


@runtime_checkable
class AgentBackend(Protocol):
    """Generic agent interface yielding a stream of ``AgentEvent`` per turn."""

    async def run_turn(
        self,
        prompt: str | ConversationMessage,
        *,
        context: AgentContext,
    ) -> AsyncIterator[AgentEvent]:
        """Process one user turn and yield events.

        Args:
            prompt: The user's input. May be a raw string or a pre-built
                ``ConversationMessage`` (e.g. with image attachments).
            context: Runtime context (cwd, model, system prompt, history).

        Yields:
            ``AgentEvent`` instances. A turn should end with exactly one
            ``AgentTurnEnd`` event (unless it ends with ``AgentError``).
        """
        ...


__all__ = ["AgentBackend"]
