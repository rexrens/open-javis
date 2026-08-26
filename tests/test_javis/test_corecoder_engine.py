"""Tests for CoreCoderEngine — the javis-side engine over corecoder.Agent.

``CoreCoderEngine`` implements the ``AgentEngine`` contract: history, usage
and event-stream turns. The inner ``corecoder.Agent`` is driven through a
scripted LLM provider, so no network is involved.
"""

from __future__ import annotations

import pytest

from javis.contracts.messages import ConversationMessage
from javis.contracts.types import (
    AgentError,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.contracts.usage import UsageSnapshot
from javis.engines.corecoder.agent import Agent
from javis.engines.corecoder.engine import CoreCoderEngine
from javis.engines.corecoder.llm import LLMResponse, ScriptedProvider, ToolCall


def _engine(script=None, **kwargs) -> CoreCoderEngine:
    llm = ScriptedProvider(script=list(script or []))
    agent = Agent(llm=llm)
    return CoreCoderEngine(
        agent,
        model="test-model",
        system_prompt="test",
        cwd="/tmp",
        max_turns=8,
        **kwargs,
    )


async def _collect(engine: CoreCoderEngine, prompt: str):
    return [e async for e in engine.submit_message(prompt)]


def test_initial_state():
    engine = _engine()
    assert engine.messages == []
    assert engine.model == "test-model"
    assert engine.system_prompt == "test"
    assert engine.max_turns == 8
    assert isinstance(engine.total_usage, UsageSnapshot)
    assert engine.tool_metadata == {}


def test_setters():
    engine = _engine()
    engine.set_model("new-model")
    assert engine.model == "new-model"
    engine.set_system_prompt("new prompt")
    assert engine.system_prompt == "new prompt"
    assert "new prompt" in engine.agent._system  # synced to inner agent
    engine.set_max_turns(16)
    assert engine.max_turns == 16
    assert engine.agent.max_rounds == 16  # synced to inner agent
    engine.set_max_turns(None)
    assert engine.max_turns is None
    engine.set_effort("high")  # no-op holder, must not raise


def test_load_messages_syncs_inner_history():
    engine = _engine()
    engine.load_messages([ConversationMessage.from_user_text("hello")])
    assert len(engine.messages) == 1
    assert engine.agent.messages == [{"role": "user", "content": "hello"}]


def test_clear_resets_inner_history():
    engine = _engine()
    engine.load_messages([ConversationMessage.from_user_text("hello")])
    engine.clear()
    assert engine.messages == []
    assert engine.agent.messages == []
    assert engine.total_usage.input_tokens == 0


@pytest.mark.asyncio
async def test_submit_message_yields_assistant_turn():
    engine = _engine([LLMResponse(content="hello world")])
    events = await _collect(engine, "hi")

    deltas = [e for e in events if isinstance(e, AgentTextDelta)]
    ends = [e for e in events if isinstance(e, AgentTurnEnd)]
    assert "".join(e.text for e in deltas) == "hello world"
    assert len(ends) == 1
    assert ends[0].text == "hello world"
    # user + assistant mirrored in javis history
    assert [m.role for m in engine.messages] == ["user", "assistant"]
    assert engine.messages[1].text == "hello world"


@pytest.mark.asyncio
async def test_submit_message_with_tool_call(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("line one\nline two\n", encoding="utf-8")
    engine = _engine([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"file_path": str(target)})]),
        LLMResponse(content="file read done"),
    ])
    events = await _collect(engine, "read the file")

    starts = [e for e in events if isinstance(e, AgentToolCallStart)]
    results = [e for e in events if isinstance(e, AgentToolCallResult)]
    assert len(starts) == 1
    assert starts[0].tool_name == "read_file"
    assert len(results) == 1
    assert not results[0].is_error
    ends = [e for e in events if isinstance(e, AgentTurnEnd)]
    assert ends[0].text == "file read done"


@pytest.mark.asyncio
async def test_submit_message_error_does_not_complete_turn():
    # ScriptedProvider raises RuntimeError when it runs out of script turns;
    # the producer forwards it as an AgentError event.
    engine = _engine()
    events = await _collect(engine, "hello")

    assert any(isinstance(e, AgentError) for e in events)
    assert not any(isinstance(e, AgentTurnEnd) for e in events)
    assert [m.role for m in engine.messages] == ["user"]


@pytest.mark.asyncio
async def test_usage_accumulates_across_turns():
    engine = _engine([LLMResponse(content="one two"), LLMResponse(content="three")])
    await _collect(engine, "first turn")
    await _collect(engine, "second turn")

    # scripted LLM reports completion tokens only; output accumulates across turns
    assert engine.total_usage.output_tokens == 3  # "one two" + "three"


@pytest.mark.asyncio
async def test_submit_message_with_conversation_message():
    engine = _engine([LLMResponse(content="ok")])
    msg = ConversationMessage.from_user_text("custom message")
    events = [e async for e in engine.submit_message(msg)]

    assert any(isinstance(e, AgentTurnEnd) for e in events)
    assert engine.messages[0].role == "user"
    assert engine.messages[0].text == "custom message"


def test_tool_metadata_is_mutable():
    engine = _engine()
    engine.tool_metadata["foo"] = "bar"
    assert engine.tool_metadata["foo"] == "bar"
