"""Session event log + SessionStore service."""
import pytest
from core import types as t
from core.session import Session, SessionStore

from javis.cordis import Context


def test_append_seq_and_whitelist():
    session = Session("s1", cwd="/tmp")
    e1 = session.append(t.SessionEvents.USER_MESSAGE, {"message": t.UserMessage.from_text("hi")})
    e2 = session.append("turn/start", {"turn": 1})
    assert e1.seq == 1 and e2.seq == 2
    assert session.events[1].data["turn"] == 1
    with pytest.raises(ValueError):
        session.append("bogus/type", {})


def test_derive_messages_skips_shadowed():
    session = Session("s1")
    user = session.append(t.SessionEvents.USER_MESSAGE, {"message": t.UserMessage.from_text("hello")})
    asst = session.append(
        t.SessionEvents.ASSISTANT_MESSAGE,
        {"message": t.AssistantMessage(content=(t.TextBlock(text="world"),))},
    )
    assert len(session.derive_messages()) == 2
    # compaction 摘要事件把前两条消息标为 shadowed
    session.append(
        t.SessionEvents.COMPACTION_SUMMARY,
        {"summary": "Earlier context (compacted): hello", "shadowedSeqs": [user.seq, asst.seq]},
    )
    session.append(
        t.SessionEvents.USER_MESSAGE,
        {"message": t.UserMessage.from_text("Earlier context (compacted): hello")},
    )
    messages = session.derive_messages()
    assert [m.text for m in messages] == ["Earlier context (compacted): hello"]


def test_events_of_find_last_last_turn():
    session = Session("s1")
    session.append("turn/start", {"turn": 1})
    session.append("turn/end", {"turn": 1})
    session.append("turn/start", {"turn": 2})
    assert session.last_turn() == 2
    assert len(session.events_of("turn/start")) == 2
    assert session.find_last("turn/end").data["turn"] == 1


def test_store_create_get_and_announce():
    ctx = Context()
    store = SessionStore(ctx)
    ctx.provide("sessions", store)
    seen = []
    ctx.on("session/created", lambda payload: seen.append(payload["session"].id))
    s = store.create(cwd="/tmp")
    assert s.id.startswith("session-")
    assert store.get(s.id) is s
    assert seen == [s.id]
    # store 生命周期（fiber effect 卸载移除）由 Task 11 组合语义覆盖，不在此单测
