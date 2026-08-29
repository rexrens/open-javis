"""Event-sourced session plugin — owns its demo-local contract.

javis has no session contract; the demo defines one here so the plugin is
self-contained: ``SESSION_SERVICE`` and the ``SessionStore`` interface live
with the plugin that provides them.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class SessionStore(ABC):
    """Append-only event-sourced session store (demo-local contract)."""

    @abstractmethod
    def create(self, session_id: str, *, cwd: str | None = None, title: str = "") -> Any:
        raise NotImplementedError

    @abstractmethod
    def get(self, session_id: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    def append(
        self,
        session_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> Any:
        raise NotImplementedError

    @abstractmethod
    def derive_messages(
        self,
        session_id: str,
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError


name = "session"
inject: list[str] = []
provides = [SessionStore]

KNOWN_EVENT_TYPES: tuple[str, ...] = (
    "turn/start",
    "step/start",
    "step/end",
    "user/message",
    "assistant/chunk",
    "assistant/message",
    "tool/result",
    "turn/end",
)


@dataclass(frozen=True)
class SessionEvent:
    seq: int
    type: str
    data: dict[str, Any]
    at: float = field(default_factory=time.time)


class Session:
    def __init__(self, session_id: str, *, cwd: str | None = None, title: str = "") -> None:
        self.id = session_id
        self.created_at = time.time()
        self.cwd = cwd
        self.title = title
        self._events: list[SessionEvent] = []

    def append(self, event_type: str, data: dict[str, Any] | None = None) -> SessionEvent:
        event = SessionEvent(
            seq=len(self._events) + 1,
            type=event_type,
            data=dict(data or {}),
        )
        self._events.append(event)
        return event

    def derive_messages(self, system_prompt: str | None = None) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for event in self._events:
            if event.type in ("user/message", "assistant/message"):
                message = event.data.get("message")
                if isinstance(message, dict):
                    messages.append(message)
            elif event.type == "tool/result":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": event.data.get("tool_call_id", ""),
                        "content": event.data.get("content", ""),
                    }
                )
        return messages

    @property
    def events(self) -> list[SessionEvent]:
        return list(self._events)


class DemoSessionService(SessionStore):
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._sessions: dict[str, Session] = {}

    def create(self, session_id: str, *, cwd: str | None = None, title: str = "") -> Session:
        if session_id in self._sessions:
            raise ValueError(f"session {session_id!r} already exists")
        session = Session(session_id, cwd=cwd, title=title)
        self._sessions[session_id] = session
        self.ctx.emit("session/created", {"session_id": session_id})
        return session

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise KeyError(f"session {session_id!r} not found") from None

    def append(
        self,
        session_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> SessionEvent:
        if event_type not in KNOWN_EVENT_TYPES:
            raise ValueError(
                f"unknown session event type {event_type!r} "
                f"(known: {', '.join(KNOWN_EVENT_TYPES)})"
            )
        event = self.get(session_id).append(event_type, data)
        self.ctx.emit("session/event", {"session_id": session_id, "event": event})
        return event

    def derive_messages(
        self,
        session_id: str,
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.get(session_id).derive_messages(system_prompt)

    def sessions(self) -> dict[str, Session]:
        return dict(self._sessions)


def apply(ctx: Any, config: Any) -> None:
    service = DemoSessionService(ctx)
    ctx.provide(SessionStore, service)
