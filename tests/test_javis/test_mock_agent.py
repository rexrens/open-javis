"""Tests for the MockAgent backend."""

from __future__ import annotations

import pytest

from javis.engine.mock_agent import MockAgent
from javis.engine.types import (
    AgentContext,
    AgentError,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)


def _context() -> AgentContext:
    return AgentContext(cwd="/tmp", model="javis-mock", system_prompt="test")


async def _collect(agent: MockAgent, prompt: str):
    return [event async for event in agent.run_turn(prompt, context=_context())]


@pytest.mark.asyncio
async def test_default_echo_response():
    agent = MockAgent()
    events = await _collect(agent, "hello")

    types = [type(e) for e in events]
    assert AgentTextDelta in types
    assert AgentTurnEnd in types
    text = "".join(e.text for e in events if isinstance(e, AgentTextDelta))
    assert "hello" in text


@pytest.mark.asyncio
async def test_tool_call_branch():
    agent = MockAgent()
    events = await _collect(agent, "please use a tool")

    types = [type(e) for e in events]
    assert AgentToolCallStart in types
    assert AgentToolCallResult in types
    start = next(e for e in events if isinstance(e, AgentToolCallStart))
    assert start.tool_name == "echo"
    result = next(e for e in events if isinstance(e, AgentToolCallResult))
    assert result.tool_name == "echo"
    assert result.is_error is False


@pytest.mark.asyncio
async def test_error_branch():
    agent = MockAgent()
    events = await _collect(agent, "trigger an error please")

    assert any(isinstance(e, AgentError) for e in events)
    # Error branch should not emit a turn end
    assert not any(isinstance(e, AgentTurnEnd) for e in events)


@pytest.mark.asyncio
async def test_status_branch():
    agent = MockAgent()
    events = await _collect(agent, "show me a status update")

    assert any(isinstance(e, AgentStatus) for e in events)
    assert any(isinstance(e, AgentTurnEnd) for e in events)


@pytest.mark.asyncio
async def test_chinese_branch():
    agent = MockAgent()
    events = await _collect(agent, "用中文回复")

    text = "".join(e.text for e in events if isinstance(e, AgentTextDelta))
    assert "中文" in text or "mock" in text.lower()
