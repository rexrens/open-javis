"""Generic agent event model — the bridge between javis and any agent backend.

These types are intentionally decoupled from OpenHarness's ``StreamEvent`` so
that a real agent implementation can emit a stable, framework-neutral event
stream. ``MockEngine`` is responsible for translating ``AgentEvent`` into
OpenHarness ``StreamEvent`` at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from openharness.engine.messages import ConversationMessage


@dataclass(frozen=True)
class AgentContext:
    """Runtime context passed to the agent for one turn."""

    cwd: str
    model: str
    system_prompt: str
    messages: list[ConversationMessage] = field(default_factory=list)
    tool_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTextDelta:
    """Incremental assistant text."""

    text: str


@dataclass(frozen=True)
class AgentToolCallStart:
    """The agent is about to execute a named tool."""

    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentToolCallResult:
    """A tool has finished executing."""

    tool_name: str
    output: str
    is_error: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class AgentTurnEnd:
    """Marks the end of one assistant turn.

    ``text`` is the final assembled assistant text for the turn. If empty,
    ``MockEngine`` will use whatever text was accumulated from
    ``AgentTextDelta`` events.
    """

    text: str = ""


@dataclass(frozen=True)
class AgentError:
    """An error that should be surfaced to the user."""

    message: str
    recoverable: bool = True


@dataclass(frozen=True)
class AgentStatus:
    """A transient status message shown to the user."""

    message: str


AgentEvent = Union[
    AgentTextDelta,
    AgentToolCallStart,
    AgentToolCallResult,
    AgentTurnEnd,
    AgentError,
    AgentStatus,
]


__all__ = [
    "AgentContext",
    "AgentError",
    "AgentEvent",
    "AgentStatus",
    "AgentTextDelta",
    "AgentToolCallResult",
    "AgentToolCallStart",
    "AgentTurnEnd",
]
