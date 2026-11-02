"""Inbox claim / clear durability + replay (alignment with upstream dsh).

``packages/core/agent/src/inbox.ts`` owns the reference semantics; this file
pins the three places the Python port had drifted:

1. a **turn-boundary** ``claim`` takes *all* ``next-step`` input *plus one*
   ``next-turn`` message (so steering submitted while idle is read by the
   opening step, not one step later);
2. **every** mutation is a durable ``agent/inbox/spliced`` record, including
   ``claim`` (a pure deletion — claiming is *not* cancelling) and ``clear``
   (deletions marked ``outcome: "canceled"``);
3. a fresh ``Inbox`` over an existing session log rebuilds the pending queues
   by replaying those records, without re-notifying observers.
"""

from __future__ import annotations

import pytest

from javis.harness.inbox import Inbox
from javis.harness.session import Session
from javis.harness.types import UserMessage


def _msg(text: str) -> UserMessage:
    return UserMessage.from_text(text)


def _texts(messages) -> list[str]:
    return [message.text for message in messages]


def _splices(session: Session) -> list[dict]:
    return [event.data for event in session.events_of("agent/inbox/spliced")]


def _splice_records(session: Session) -> list[dict]:
    """Splice payloads with message lists flattened to text (readable asserts)."""
    records = []
    for event in session.events_of("agent/inbox/spliced"):
        record = dict(event.data)
        record["inserted"] = _texts(record.get("inserted", []))
        records.append(record)
    return records


def _recorder() -> tuple[list, list, list]:
    """(inserted, claimed, discarded) sink lists for the Inbox notifications."""
    return [], [], []


# ---------------------------------------------------------------------------
# 1. claim boundaries
# ---------------------------------------------------------------------------


def test_turn_boundary_claim_takes_all_next_step_then_one_next_turn():
    session = Session("s1")
    inbox = Inbox(session)
    inbox.splice("next-step", 0, 0, [_msg("steer A"), _msg("steer B")])
    inbox.splice("next-turn", 0, 0, [_msg("followup 1")])
    inbox.splice("next-turn", 1, 0, [_msg("followup 2")])

    claimed = inbox.claim("next-turn", 7)

    # next-step input comes first, the queued turn last (upstream order).
    assert _texts(claimed) == ["steer A", "steer B", "followup 1"]
    assert _texts(inbox.next_step) == []
    assert _texts(inbox.next_turn) == ["followup 2"]


def test_turn_boundary_claim_with_no_queued_turn_takes_steering_only():
    session = Session("s1")
    inbox = Inbox(session)
    inbox.splice("next-step", 0, 0, [_msg("steer only")])

    claimed = inbox.claim("next-turn", 1)

    assert _texts(claimed) == ["steer only"]
    assert inbox.has_pending is False
    # an empty next-turn queue is not a splice: one insert, one deletion.
    assert _splice_records(session) == [
        {"target": "next-step", "start": 0, "inserted": ["steer only"]},
        {"target": "next-step", "start": 0, "removedCount": 1, "inserted": []},
    ]


def test_step_boundary_claim_takes_next_step_only():
    session = Session("s1")
    inbox = Inbox(session)
    inbox.splice("next-step", 0, 0, [_msg("steer A")])
    inbox.splice("next-turn", 0, 0, [_msg("followup 1")])
    inbox.splice("next-turn", 1, 0, [_msg("followup 2")])

    claimed = inbox.claim("next-step", 7)

    assert _texts(claimed) == ["steer A"]
    assert _texts(inbox.next_turn) == ["followup 1", "followup 2"]


def test_claim_publishes_each_message_with_its_turn():
    session = Session("s1")
    inserted, claimed, _ = _recorder()
    inbox = Inbox(session, inserted=inserted.append, claimed=lambda m, turn: claimed.append((m, turn)))
    inbox.splice("next-step", 0, 0, [_msg("steer")])
    inbox.splice("next-turn", 0, 0, [_msg("followup")])

    inbox.claim("next-turn", 3)

    assert [(message.text, turn) for message, turn in claimed] == [("steer", 3), ("followup", 3)]


# ---------------------------------------------------------------------------
# 2. durable splices
# ---------------------------------------------------------------------------


def test_plain_splice_records_the_normalized_insert():
    session = Session("s1")
    inbox = Inbox(session)

    inbox.splice("next-step", 0, 0, [_msg("a"), _msg("b")])

    record = _splices(session)[0]
    assert record["target"] == "next-step"
    assert record["start"] == 0
    assert _texts(record["inserted"]) == ["a", "b"]
    assert "removedCount" not in record
    assert "outcome" not in record


def test_claim_records_a_pure_deletion_without_discarding():
    session = Session("s1")
    _, _, discarded = _recorder()
    inbox = Inbox(session, discarded=discarded.append)
    inbox.splice("next-turn", 0, 0, [_msg("followup")])

    inbox.claim("next-turn", 3)

    # claiming is not cancelling: no discard notification ...
    assert discarded == []
    # ... but the removal itself is durable.
    assert _splices(session)[-1] == {
        "target": "next-turn",
        "start": 0,
        "removedCount": 1,
        "inserted": [],
    }


def test_clear_records_canceled_splices_and_notifies_discarded():
    session = Session("s1")
    _, _, discarded = _recorder()
    inbox = Inbox(session, discarded=discarded.append)
    inbox.splice("next-step", 0, 0, [_msg("steer")])
    inbox.splice("next-turn", 0, 0, [_msg("followup")])

    removed = inbox.clear()

    assert removed == 2
    # upstream clear() order: next-step first, then next-turn.
    assert _texts(discarded) == ["steer", "followup"]
    step_record, turn_record = _splices(session)[-2:]
    assert step_record == {
        "target": "next-step",
        "start": 0,
        "removedCount": 1,
        "inserted": [],
        "outcome": "canceled",
    }
    assert turn_record["target"] == "next-turn"
    assert turn_record["outcome"] == "canceled"
    assert inbox.has_pending is False


# ---------------------------------------------------------------------------
# 3. replay from the log
# ---------------------------------------------------------------------------


def test_new_inbox_replays_pending_messages_without_renotifying():
    session = Session("s1")
    Inbox(session).splice("next-step", 0, 0, [_msg("pending steer")])
    Inbox(session).splice("next-turn", 0, 0, [_msg("pending followup")])

    inserted, claimed, discarded = _recorder()
    revived = Inbox(
        session,
        inserted=inserted.append,
        claimed=lambda m, turn: claimed.append(m),
        discarded=discarded.append,
    )

    assert _texts(revived.next_step) == ["pending steer"]
    assert _texts(revived.next_turn) == ["pending followup"]
    # replay restores state quietly — no observer sees old insertions again.
    assert inserted == [] and claimed == [] and discarded == []


def test_replay_reflects_claims_and_clears():
    session = Session("s1")
    live = Inbox(session)
    live.splice("next-turn", 0, 0, [_msg("claimed"), _msg("stays")])

    live.claim("next-turn", 1)
    assert _texts(Inbox(session).next_turn) == ["stays"]

    live.clear()
    assert _texts(Inbox(session).next_turn) == []
    assert _texts(Inbox(session).next_step) == []


def test_replay_rejects_a_malformed_splice():
    session = Session("s1")
    session.append(
        "agent/inbox/spliced",
        {"target": "next-step", "start": 5, "removedCount": 0, "inserted": []},
    )

    with pytest.raises(ValueError, match="invalid inbox splice"):
        Inbox(session)
