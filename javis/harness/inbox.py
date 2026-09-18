"""Inbox: the agent-owned projection of durable pending work.

Port of ``packages/core/agent/src/inbox.ts`` (dsh): two FIFO queues —
``next-turn`` (follow-ups) and ``next-step`` (steering) — with splice
semantics, claim-at-boundary, and durable splice logging on the session
(``agent/inbox/spliced``). Every mutation notifies the callbacks the agent
wires to its event dispatch (``agent/inbox/inserted|claimed|discarded``).

Splice signature mirrors the JavaScript original::

    splice(target, start, deleteCount, items)

Two rules keep the session log the source of truth for the queues:

- **claim** is the boundary batch consumer. Every boundary consumes *all*
  ``next-step`` input; a turn boundary (``target == "next-turn"``) additionally
  consumes **one** ``next-turn`` message. Its records are *pure deletions* —
  claiming is not cancelling, so nothing notifies as discarded.
- **every** mutation is one ``agent/inbox/spliced`` record, written before the
  live queues change. A fresh :class:`Inbox` over the same session therefore
  rebuilds the pending queues by replaying those records, quietly.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .session import Session
from .types import InboxTarget, UserMessage

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
        # Replay the durable records so a rebuilt Inbox resumes with the same
        # pending work (upstream applies every splice after the seed prefix).
        # Replay is silent: observers already saw the original insertions.
        for event in session.events_of("agent/inbox/spliced"):
            self._apply(event.data)

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

    # -- durable splice records ----------------------------------------------

    def _validate(self, record: dict[str, Any]) -> tuple[InboxTarget, int, int, list[Any]]:
        """Bounds-check one splice record (used by the live path and replay).

        A malformed record is fatal: ``ValueError`` here means the session log
        itself is inconsistent, which must not be papered over.
        """
        target = record.get("target")
        start = record.get("start")
        removed_count = record.get("removedCount", 0)
        inserted = record.get("inserted", [])
        if target not in ("next-turn", "next-step"):
            raise ValueError(f"invalid inbox splice: bad target {target!r}")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or start < 0
            or not isinstance(removed_count, int)
            or isinstance(removed_count, bool)
            or removed_count < 0
            or not isinstance(inserted, list)
            or start + removed_count > len(self._queue(target))
        ):
            raise ValueError(f"invalid inbox splice: {record!r}")
        return target, start, removed_count, inserted

    def _apply(self, record: dict[str, Any]) -> list[UserMessage]:
        """Apply one splice record to the live queues (no notifications)."""
        target, start, removed_count, inserted = self._validate(record)
        queue = self._queue(target)
        removed = queue[start : start + removed_count]
        queue[start : start + removed_count] = list(inserted)
        return removed

    def _record(self, record: dict[str, Any], *, discard_removed: bool) -> list[UserMessage]:
        """Commit one splice: validate, log, then apply and notify.

        The durable record is written *before* the live queues mutate, so a
        synchronous ``session/event`` observer still sees the pre-splice queues
        and can rebuild the removed messages from the normalized coordinates.
        """
        self._validate(record)
        self._session.append("agent/inbox/spliced", record)
        removed = self._apply(record)
        if discard_removed:
            for message in removed:
                if self._discarded is not None:
                    self._discarded(message)
        for message in record["inserted"]:
            if self._inserted is not None:
                self._inserted(message)
        return removed

    def _delete(self, target: InboxTarget, start: int, count: int) -> list[UserMessage]:
        """Durably remove messages without notifying them as discarded.

        Clamped like the upstream ``mutate``: asking for a message the queue
        does not have removes nothing and writes no record.
        """
        count = min(count, len(self._queue(target)) - start)
        if count <= 0:
            return []
        return self._record(
            {"target": target, "start": start, "removedCount": count, "inserted": []},
            discard_removed=False,
        )

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
        queue length). Returns the removed messages, which notify as discarded
        (this is the cancelling path — ``claim`` is the pure-deletion one).
        """
        queue = self._queue(target)
        if start < 0 or start > len(queue):
            raise IndexError(f"inbox {target}: start {start} out of range")
        record: dict[str, Any] = {"target": target, "start": start, "inserted": list(items)}
        if delete_count:
            record["removedCount"] = delete_count
            record["outcome"] = "canceled"
        return self._record(record, discard_removed=True)

    def claim(self, target: InboxTarget, turn: int) -> list[UserMessage]:
        """Take the batch proposed for this boundary (upstream ``claim``).

        Every boundary consumes all ``next-step`` input; ``target ==
        "next-turn"`` additionally consumes *one* ``next-turn`` message — that
        is what lets an idle ``steer()`` be read by the opening step instead of
        costing a context-only model call. Returns ``next-step`` input followed
        by the queued turn, and publishes each message with ``turn``.
        """
        self._queue(target)
        claimed = self._delete("next-step", 0, len(self._next_step))
        if target == "next-turn":
            claimed += self._delete("next-turn", 0, 1)
        for message in claimed:
            if self._claimed is not None:
                self._claimed(message, turn)
        return claimed

    def clear(self) -> int:
        """Discard every queued message; returns how many were removed.

        ``clear`` is the cancelling path: both records carry
        ``outcome: "canceled"`` and every removed message notifies as
        discarded. Empty queues write nothing (upstream skips no-op splices).
        """
        removed = 0
        for target, queue in (("next-step", self._next_step), ("next-turn", self._next_turn)):
            if queue:
                removed += len(
                    self._record(
                        {
                            "target": target,
                            "start": 0,
                            "removedCount": len(queue),
                            "inserted": [],
                            "outcome": "canceled",
                        },
                        discard_removed=True,
                    )
                )
        return removed


__all__ = ["Inbox"]
