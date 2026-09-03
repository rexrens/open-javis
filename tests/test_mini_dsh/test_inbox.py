"""Inbox next-turn / next-step dual queue semantics."""
from core import types as t
from core.inbox import Inbox
from core.session import Session


def _msg(text: str) -> t.UserMessage:
    return t.UserMessage.from_text(text)


def test_turn_vs_step_targets():
    inbox = Inbox(Session("s1"))
    inbox.next_turn.append(_msg("a"))
    inbox.next_step.append(_msg("b"))
    assert [m.text for m in inbox.claim("next-turn", 1)] == ["a"]
    assert [m.text for m in inbox.claim("next-step", 1)] == ["b"]
    assert not inbox.has_pending


def test_splice_ordering():
    inbox = Inbox(Session("s1"))
    inbox.next_step.append(_msg("a"))
    inbox.next_step.append(_msg("c"))
    inbox.splice("next-step", 1, 0, [_msg("b")])
    assert [m.text for m in inbox.claim("next-step", 1)] == ["a", "b", "c"]
