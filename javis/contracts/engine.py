"""Agent engine protocol — the single engine seam.

The host (runtime / TUI / commands) talks to exactly one object: an
``AgentEngine`` that owns conversation history and usage, and yields
``AgentEvent`` streams per turn. The built-in implementation is
``javis.engines.corecoder.engine.CoreCoderEngine``; engine plugins provide an
instance of this protocol under the ``engine`` service (see
``javis.contracts.services``) to replace it.

This replaces the old two-level seam (``AgentBackend`` protocol + a
``QueryEngine`` shell) with a single contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from javis.contracts.messages import ConversationMessage
from javis.contracts.types import AgentEvent
from javis.contracts.usage import UsageSnapshot


@runtime_checkable
class AgentEngine(Protocol):
    """One engine object: history + usage + event-stream turns.

    Optional hooks (probed with ``hasattr``, NOT part of the Protocol so a
    minimal implementation can skip them):

        def load_history(self, messages: list[ConversationMessage]) -> None:
            '''Rebuild engine-internal history from javis mirror messages.'''

        def clear_history(self) -> None:
            '''Clear engine-internal history.'''

        def set_permission_checker(self, checker) -> None:
            '''Attach the host's async permission hook
            ``checker(tool_name, arguments) -> "allow" | deny-reason``.
            The host calls this on startup when present; implementations that
            execute tools (e.g. corecoder's agent loop) forward it to their
            tool-execution path so the TUI's ask/deny flow keeps working.
            '''
    """

    @property
    def messages(self) -> list[ConversationMessage]: ...
    @property
    def total_usage(self) -> UsageSnapshot: ...
    @property
    def model(self) -> str: ...
    @property
    def system_prompt(self) -> str: ...
    @property
    def max_turns(self) -> int | None: ...
    @property
    def tool_metadata(self) -> dict[str, Any]: ...

    def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[AgentEvent]:
        """Submit one user turn and yield events (ends with ``AgentTurnEnd``).

        Declared as a plain method returning an async iterator (not ``async
        def``) so the protocol reads as "an async-iterable event stream";
        implementations provide it as an async generator.
        """
        ...

    def clear(self) -> None: ...
    def load_messages(self, messages: list[ConversationMessage]) -> None: ...
    def set_system_prompt(self, prompt: str) -> None: ...
    def set_max_turns(self, max_turns: int | None) -> None: ...
    def set_model(self, model: str) -> None: ...
    def set_effort(self, effort: str | None) -> None: ...


__all__ = ["AgentEngine"]
