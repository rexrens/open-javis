"""Tests for the MockEngine adapter."""

from __future__ import annotations

import pytest

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StatusEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)

from javis.engine.mock_agent import MockAgent
from javis.engine.mock_engine import MockEngine


def _engine(prompt: str = "") -> MockEngine:
    return MockEngine(
        MockAgent(),
        model="javis-mock",
        system_prompt="test",
        cwd="/tmp",
        max_turns=8,
    )


def test_initial_state():
    engine = _engine()
    assert engine.messages == []
    assert engine.model == "javis-mock"
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
    engine.set_api_client(None)
    engine.set_permission_checker(None)


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

    assert any(isinstance(e, AssistantTextDelta) for e in events)
    assert any(isinstance(e, AssistantTurnComplete) for e in events)
    # User message + assistant message
    assert len(engine.messages) == 2
    assert engine.messages[0].role == "user"
    assert engine.messages[1].role == "assistant"
    complete = next(e for e in events if isinstance(e, AssistantTurnComplete))
    assert complete.message.role == "assistant"


@pytest.mark.asyncio
async def test_submit_message_with_tool_call():
    engine = _engine()
    events = [e async for e in engine.submit_message("use a tool please")]

    assert any(isinstance(e, ToolExecutionStarted) for e in events)
    assert any(isinstance(e, ToolExecutionCompleted) for e in events)
    started = next(e for e in events if isinstance(e, ToolExecutionStarted))
    assert started.tool_name == "echo"


@pytest.mark.asyncio
async def test_submit_message_error_does_not_complete_turn():
    engine = _engine()
    events = [e async for e in engine.submit_message("trigger an error")]

    assert any(isinstance(e, ErrorEvent) for e in events)
    # No AssistantTurnComplete on error
    assert not any(isinstance(e, AssistantTurnComplete) for e in events)
    # Only the user message is appended
    assert len(engine.messages) == 1


@pytest.mark.asyncio
async def test_submit_message_with_conversation_message():
    engine = _engine()
    msg = ConversationMessage.from_user_text("custom message")
    events = [e async for e in engine.submit_message(msg)]

    assert any(isinstance(e, AssistantTurnComplete) for e in events)
    assert engine.messages[0].role == "user"
    assert engine.messages[0].text == "custom message"


@pytest.mark.asyncio
async def test_continue_pending_is_noop():
    engine = _engine()
    events = [e async for e in engine.continue_pending(max_turns=4)]
    assert len(events) == 1
    assert isinstance(events[0], StatusEvent)


def test_tool_metadata_is_mutable():
    engine = _engine()
    engine.tool_metadata["foo"] = "bar"
    assert engine.tool_metadata["foo"] == "bar"
