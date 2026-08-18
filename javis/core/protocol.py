"""Agent backend protocol — the seam where a real agent plugs in.

Implement this ``Protocol`` to wire a custom agent into the javis TUI.
``CoreCoderBackend`` is the built-in implementation; swap in your own
without touching ``QueryEngine`` or any TUI code.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from javis.messages import ConversationMessage

from javis.core.types import AgentContext, AgentEvent


@runtime_checkable
class AgentBackend(Protocol):
    """Generic agent interface yielding a stream of ``AgentEvent`` per turn.

    Optional hooks (documented here, NOT in the Protocol class — runtime_checkable
    isinstance() checks member presence, so optional members would break
    backends that don't implement them; the engine layer probes with hasattr):

        def load_history(self, messages: list[ConversationMessage]) -> None:
            '''Rebuild engine-internal history from javis mirror messages.'''

        def clear_history(self) -> None:
            '''Clear engine-internal history.'''
    """

    async def run_turn(
        self,
        prompt: str | ConversationMessage,
        *,
        context: AgentContext,
    ) -> AsyncIterator[AgentEvent]:
        """Process one user turn and yield events.

        A turn should end with exactly one ``AgentTurnEnd`` event (unless it
        ends with ``AgentError``).
        """
        ...


__all__ = ["AgentBackend"]
