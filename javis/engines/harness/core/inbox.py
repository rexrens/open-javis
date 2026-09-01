"""Inbox: the agent-owned projection of durable pending work.

Port of ``packages/core/agent/src/inbox.ts`` (dsh): two FIFO queues —
``next-turn`` (follow-ups) and ``next-step`` (steering) — with splice
semantics, claim-at-boundary, and durable splice logging on the session
(``agent/inbox/spliced``). Every mutation notifies the callbacks the agent
wires to its event dispatch (``agent/inbox/inserted|claimed|discarded``).

Splice signature mirrors the JavaScript original::

    splice(target, start, deleteCount, items)
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .contracts import InboxTarget, UserMessage
from .session import Session

Callback = Callable[..., Any]


class Inbox:
    """Queued user input, split by the boundary it targets."""

    def __init__(
        self,
        session: Session,
        *,
        inserted: Callback | None = None,
        claimed: Callback | None = None,
        discarded: Callback | None = None,
    ) -> None:
        self._session = session
        self._inserted = inserted
        self._claimed = claimed
        self._discarded = discarded
        self._next_turn: list[UserMessage] = []
        self._next_step: list[UserMessage] = []

    # -- queues --------------------------------------------------------------

    @property
    def next_turn(self) -> list[UserMessage]:
        return list(self._next_turn)

    @property
    def next_step(self) -> list[UserMessage]:
        return list(self._next_step)

    @property
    def has_pending(self) -> bool:
        return bool(self._next_turn or self._next_step)

    def _queue(self, target: InboxTarget) -> list[UserMessage]:
        if target == "next-turn":
            return self._next_turn
        if target == "next-step":
            return self._next_step
        raise ValueError(f"invalid inbox target {target!r}")

    # -- mutations -----------------------------------------------------------

    def splice(
        self,
        target: InboxTarget,
        start: int,
        delete_count: int,
        items: Sequence[UserMessage],
    ) -> list[UserMessage]:
        """Insert ``items`` at ``start``, removing ``delete_count`` first.

        ``start == len(queue)`` appends (the dsh wake pattern uses
        ``splice(target, Infinity, 0, [message])``; Python callers pass the
        queue length). Returns the removed messages.
        """
        queue = self._queue(target)
        if start < 0 or start > len(queue):
            raise IndexError(f"inbox {target}: start {start} out of range")
        removed = queue[start : start + delete_count]
        queue[start : start + delete_count] = list(items)
        self._session.append(
            "agent/inbox/spliced",
            {
                "target": target,
                "start": start,
                "removed": removed,
                "inserted": list(items),
            },
        )
        for message in removed:
            if self._discarded is not None:
                self._discarded(message)
        for message in items:
            if self._inserted is not None:
                self._inserted(message)
        return removed

    def claim(self, target: InboxTarget, turn: int) -> list[UserMessage]:
        """Take every message queued for ``target`` (the boundary consumes them)."""
        queue = self._queue(target)
        claimed = list(queue)
        del queue[:]
        for message in claimed:
            if self._claimed is not None:
                self._claimed(message, turn)
        return claimed

    def clear(self) -> int:
        """Discard every queued message; returns how many were removed."""
        removed = self._next_turn + self._next_step
        self._next_turn.clear()
        self._next_step.clear()
        for message in removed:
            if self._discarded is not None:
                self._discarded(message)
        return len(removed)


__all__ = ["Inbox"]
