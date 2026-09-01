"""Session: the append-only event log and durable source of truth.

Port of ``packages/core/session`` (dsh): every conversation fact — turn/step
boundaries, user/assistant/tool messages, request headers, inbox splices — is
an immutable :class:`SessionEvent` with a monotonically increasing ``seq``.
The agent loop *appends*; consumers (UI bridges, replay, tests) read the log
and rebuild state with :meth:`Session.derive_messages`.

Only the vocabulary in ``contracts.SESSION_EVENT_TYPES`` is accepted — the
per-event ``ignorable`` guard of dsh is subsumed by this whitelist for the
demo (unknown event types fail at append time, not at read time).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .contracts import (
    SESSION_EVENT_TYPES,
    SESSION_FORMAT_VERSION,
    SessionId,
    ToolResultMessage,
)


@dataclass
class SessionHeader:
    """Immutable validated storage metadata, kept outside the event log."""

    version: int
    id: SessionId
    #: Non-negative safe-integer Unix epoch milliseconds.
    created_at: int
    #: Absolute working directory the session was created in (if any).
    cwd: str | None = None


@dataclass
class SessionEvent:
    """One immutable event in the session log."""

    seq: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    #: Seqs of the events this one cites (e.g. a result cites its call).
    source_event_seqs: tuple[int, ...] = ()


class Session:
    """An append-only event log; ``session.append()`` is the only write path."""

    def __init__(self, id: SessionId, cwd: str | None = None) -> None:
        self.id = id
        self.header = SessionHeader(
            version=SESSION_FORMAT_VERSION,
            id=id,
            created_at=int(time.time() * 1000),
            cwd=cwd,
        )
        self._events: list[SessionEvent] = []
        self._seq = 0

    # -- read ----------------------------------------------------------------

    @property
    def events(self) -> list[SessionEvent]:
        return list(self._events)

    def find_last(self, type: str) -> SessionEvent | None:
        for event in reversed(self._events):
            if event.type == type:
                return event
        return None

    def events_of(self, type: str) -> list[SessionEvent]:
        return [event for event in self._events if event.type == type]

    def last_turn(self) -> int:
        event = self.find_last("turn/start")
        return int(event.data["turn"]) if event else 0

    #: Rebuild the conversation exactly as the model sees it (dsh ``deriveMessages``).
    def derive_messages(self) -> list[Any]:
        out: list[Any] = []
        for event in self._events:
            if event.type in ("user/message", "assistant/message", "tool/result"):
                out.append(event.data["message"])
        return out

    def request_header(self) -> dict[str, Any] | None:
        """The last canonical request header (config snapshot), if any."""
        event = self.find_last("request/header")
        return event.data["header"] if event else None

    def request_context(self) -> dict[str, Any] | None:
        """The last request context (provider/model/contextWindow), if any."""
        event = self.find_last("request/context")
        return event.data if event else None

    def usage_total(self) -> tuple[int, int]:
        """Sum ``(input_tokens, output_tokens)`` over every assistant message."""
        total_in = total_out = 0
        for event in self.events_of("assistant/message"):
            usage = event.data.get("usage")
            if usage:
                total_in += usage.input_tokens
                total_out += usage.output_tokens
        return total_in, total_out

    # -- write ---------------------------------------------------------------

    def append(self, type: str, data: dict[str, Any] | None = None, **options: Any) -> SessionEvent:
        """Append one event; returns it (with its new ``seq``).

        Unknown event types raise ``ValueError`` (dsh logs them under the
        ``ignorable`` guard; the demo fails fast instead).
        """
        if type not in SESSION_EVENT_TYPES:
            raise ValueError(f"session {self.id}: unknown event type {type!r}")
        self._seq += 1
        event = SessionEvent(
            seq=self._seq,
            type=type,
            data=dict(data or {}),
            source_event_seqs=tuple(options.get("sourceEventSeqs") or ()),
        )
        self._events.append(event)
        return event


def create_tool_result_message(call_id: str, content: list[Any], is_error: bool = False) -> ToolResultMessage:
    """dsh ``createToolResultMessage`` — one durable tool-result message."""
    return ToolResultMessage.for_call(call_id, content, is_error)


__all__ = ["Session", "SessionEvent", "SessionHeader", "create_tool_result_message"]
