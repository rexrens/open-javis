"""Tests for the QueryEngine adapter."""

from __future__ import annotations

import pytest

from tests.test_javis.fake_backend import FakeBackend
from javis.core.query_engine import QueryEngine
from javis.core.types import (
    AgentError,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.messages import ConversationMessage
from javis.usage import UsageSnapshot


def _engine(prompt: str = "") -> QueryEngine:
    return QueryEngine(
        FakeBackend(),
        model="test-model",
        system_prompt="test",
        cwd="/tmp",
        max_turns=8,
    )


def test_initial_state():
    engine = _engine()
    assert engine.messages == []
    assert engine.model == "test-model"
    assert engine.system_prompt == "test"
    assert engine.max_turns == 8
    assert engine.has_pending_continuation() is False
    assert isinstance(engine.total_usage, UsageSnapshot)


def test_setters_are_noop_or_assign():
    engine = _engine()
    engine.set_model("new-model")
    assert engine.model == "new-model"
    engine.set_system_prompt("new prompt")
    assert engine.system_prompt == "new prompt"
    engine.set_max_turns(16)
    assert engine.max_turns == 16
    engine.set_max_turns(None)
    assert engine.max_turns is None
    # These should not raise
    engine.set_effort("high")


def test_load_and_clear_messages():
    engine = _engine()
    msg = ConversationMessage.from_user_text("hello")
    engine.load_messages([msg])
    assert len(engine.messages) == 1
    engine.clear()
    assert engine.messages == []


@pytest.mark.asyncio
async def test_submit_message_yields_assistant_turn():
    engine = _engine()
    events = [e async for e in engine.submit_message("hello")]

    assert any(isinstance(e, AgentTextDelta) for e in events)
    assert any(isinstance(e, AgentTurnEnd) for e in events)
    # User message + assistant message
    assert len(engine.messages) == 2
    assert engine.messages[0].role == "user"
    assert engine.messages[1].role == "assistant"
    complete = next(e for e in events if isinstance(e, AgentTurnEnd))
    assert complete.text


@pytest.mark.asyncio
async def test_submit_message_with_tool_call():
    engine = _engine()
    events = [e async for e in engine.submit_message("use a tool please")]

    assert any(isinstance(e, AgentToolCallStart) for e in events)
    assert any(isinstance(e, AgentToolCallResult) for e in events)
    started = next(e for e in events if isinstance(e, AgentToolCallStart))
    assert started.tool_name == "echo"


@pytest.mark.asyncio
async def test_submit_message_error_does_not_complete_turn():
    engine = _engine()
    events = [e async for e in engine.submit_message("trigger an error")]

    assert any(isinstance(e, AgentError) for e in events)
    # No AgentTurnEnd on error
    assert not any(isinstance(e, AgentTurnEnd) for e in events)
    # Only the user message is appended
    assert len(engine.messages) == 1


@pytest.mark.asyncio
async def test_submit_message_with_conversation_message():
    engine = _engine()
    msg = ConversationMessage.from_user_text("custom message")
    events = [e async for e in engine.submit_message(msg)]

    assert any(isinstance(e, AgentTurnEnd) for e in events)
    assert engine.messages[0].role == "user"
    assert engine.messages[0].text == "custom message"


@pytest.mark.asyncio
async def test_continue_pending_is_noop():
    engine = _engine()
    events = [e async for e in engine.continue_pending(max_turns=4)]
    assert len(events) == 1
    assert isinstance(events[0], AgentStatus)


def test_tool_metadata_is_mutable():
    engine = _engine()
    engine.tool_metadata["foo"] = "bar"
    assert engine.tool_metadata["foo"] == "bar"


class UsageBackend:
    """Backend that reports real usage in AgentTurnEnd."""

    async def run_turn(self, prompt, *, context):
        yield AgentTextDelta(text="hi")
        yield AgentTurnEnd(text="hi", usage=UsageSnapshot(input_tokens=5, output_tokens=7))


@pytest.mark.asyncio
async def test_submit_message_uses_backend_usage():
    engine = QueryEngine(UsageBackend(), model="m", system_prompt="s", cwd="/tmp")
    [e async for e in engine.submit_message("hello")]
    assert engine.total_usage.input_tokens == 5
    assert engine.total_usage.output_tokens == 7


class HookBackend(UsageBackend):
    def __init__(self) -> None:
        self.cleared = False

    def clear_history(self) -> None:
        self.cleared = True


@pytest.mark.asyncio
async def test_clear_forwards_to_backend_hook():
    backend = HookBackend()
    engine = QueryEngine(backend, model="m", system_prompt="s", cwd="/tmp")
    engine.clear()
    assert backend.cleared is True
