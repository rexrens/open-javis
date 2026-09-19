"""Session log persistence, projection and resume."""

from __future__ import annotations

import pytest

from harness.session import (
    SESSION_VERSION,
    Session,
    SessionError,
    SessionService,
    assistant_event,
    tool_call_event,
    tool_result_event,
    turn_end_event,
    turn_start_event,
    user_event,
)
from harness.types import tool_call_block


def _service(tmp_path) -> SessionService:
    return SessionService(ctx=None, directory=tmp_path / "sessions")


def test_create_writes_a_header_and_appends_events(tmp_path):
    sessions = _service(tmp_path)
    session = sessions.create("/work", "fake", "fake-model")
    assert session.path.is_file()
    first_line = session.path.read_text(encoding="utf-8").splitlines()[0]
    assert f'"version": {SESSION_VERSION}' in first_line
    assert '"cwd": "/work"' in first_line

    session.append(user_event("hi"))
    session.append(assistant_event([{"type": "text", "text": "hello"}]))
    assert [event["type"] for event in session.events()] == ["user/message", "assistant/message"]


def test_messages_project_model_history(tmp_path):
    sessions = _service(tmp_path)
    session = sessions.create("/work", "fake", "fake-model")
    session.append(turn_start_event(1))
    session.append(user_event("list files"))
    session.append(assistant_event([tool_call_block("call-1", "bash", '{"command":"ls"}')]))
    session.append(tool_call_event("call-1", "bash", '{"command":"ls"}'))
    session.append(tool_result_event("call-1", "bash", "a.txt\n[exit code: 0]"))
    session.append(assistant_event([{"type": "text", "text": "one file"}]))
    session.append(turn_end_event(1))

    messages = session.messages()
    assert [message["role"] for message in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[1]["content"][0]["type"] == "tool-call"
    assert messages[2]["content"][0]["toolCallId"] == "call-1"
    assert messages[3]["content"][0]["text"] == "one file"


def test_error_tool_results_stay_in_history(tmp_path):
    sessions = _service(tmp_path)
    session = sessions.create("/work", "fake", "fake-model")
    session.append(tool_result_event("call-9", "bash", "boom", is_error=True))
    block = session.messages()[0]["content"][0]
    assert block["isError"] is True
    assert block["content"][0]["text"] == "boom"


def test_open_round_trips_and_resume_keeps_identity(tmp_path):
    sessions = _service(tmp_path)
    session = sessions.create("/work", "fake", "fake-model")
    session.append(user_event("first"))
    session.append(assistant_event([{"type": "text", "text": "answer"}]))
    session.append(turn_start_event(1))

    reopened = sessions.open(session.id)
    assert reopened.id == session.id
    assert reopened.cwd == "/work"
    assert reopened.turns == 1
    assert len(reopened.messages()) == 2


def test_list_and_latest_order_by_recency(tmp_path):
    sessions = _service(tmp_path)
    first = sessions.create("/one", "fake", "fake-model")
    second = sessions.create("/two", "fake", "fake-model")
    import os
    import time

    now = time.time()
    os.utime(first.path, (now - 60, now - 60))
    os.utime(second.path, (now, now))
    listed = sessions.list()
    assert [info.id for info in listed] == [second.id, first.id]
    assert sessions.latest().id == second.id


def test_missing_and_invalid_sessions_raise(tmp_path):
    sessions = _service(tmp_path)
    with pytest.raises(SessionError):
        sessions.open("does-not-exist")

    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    broken = tmp_path / "sessions" / "broken.jsonl"
    broken.write_text("not json\n", encoding="utf-8")
    with pytest.raises(SessionError):
        sessions.open("broken")

    future = tmp_path / "sessions" / "future.jsonl"
    future.write_text('{"type": "session/header", "version": 99}\n', encoding="utf-8")
    with pytest.raises(SessionError):
        sessions.open("future")


def test_latest_is_none_without_sessions(tmp_path):
    assert _service(tmp_path).latest() is None
    assert Session.create(tmp_path / "sessions", ".", "fake", "m").turns == 0


def test_the_service_broadcasts_every_committed_event(tmp_path):
    """The session service owns the durable firehose, not its callers."""
    from cordis_harness_support.stub import StubContext

    ctx = StubContext()
    sessions = SessionService(ctx, tmp_path / "sessions")
    session = sessions.create("/work", "fake", "fake-model")
    session.append(user_event("hi"))
    session.append(assistant_event([{"type": "text", "text": "hello"}]))

    assert [name for name, _ in ctx.events] == ["session/event", "session/event"]
    session_arg, event = ctx.events[0][1]
    assert session_arg is session
    assert event["type"] == "user/message"
    assert event["content"] == [{"type": "text", "text": "hi"}]

    # A reopened session keeps broadcasting through the same service.
    reopened = sessions.open(session.id)
    reopened.append(turn_end_event(1))
    assert len(ctx.events) == 3


def test_a_session_without_a_context_still_works(tmp_path):
    """Programmatic use without the service (no firehose) must keep working."""
    session = Session.create(tmp_path / "sessions", ".", "fake", "m")
    session.append(user_event("hi"))
    assert len(session.messages()) == 1
