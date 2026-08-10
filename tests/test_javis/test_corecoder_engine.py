"""Tests for the CoreCoderEngine — javis's real agent engine."""

from __future__ import annotations

import asyncio

import pytest

from openharness.engine.messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)

from javis.corecoder.agent import Agent
from javis.corecoder.llm import LLMResponse, ScriptedLLM, ToolCall
from javis.corecoder.tools import ALL_TOOLS
from javis.engine.corecoder_engine import CoreCoderEngine, _to_corecoder_messages


def _make_engine(script: list[LLMResponse], *, max_rounds: int = 10, **kwargs) -> CoreCoderEngine:
    llm = ScriptedLLM(script=script)
    agent = Agent(llm=llm, tools=ALL_TOOLS, max_rounds=max_rounds)
    return CoreCoderEngine(
        agent=agent,
        model="test-model",
        system_prompt="test system prompt",
        cwd="/tmp",
        **kwargs,
    )


async def _collect(engine: CoreCoderEngine, prompt: str):
    events = []
    async for event in engine.submit_message(prompt):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_plain_text_reply():
    engine = _make_engine([LLMResponse(content="hello world")])
    events = await _collect(engine, "hi")

    deltas = [e for e in events if isinstance(e, AssistantTextDelta)]
    complete = [e for e in events if isinstance(e, AssistantTurnComplete)]
    assert "".join(e.text for e in deltas) == "hello world"
    assert len(complete) == 1
    assert complete[0].message.text == "hello world"
    # history is updated for the UI
    assert engine.messages[-1].role == "assistant"
    assert engine.messages[-1].text == "hello world"


@pytest.mark.asyncio
async def test_tool_call_round(tmp_path):
    target = tmp_path / "hello.txt"
    target.write_text("line one\nline two\n", encoding="utf-8")

    engine = _make_engine([
        LLMResponse(
            tool_calls=[
                ToolCall(id="call_1", name="read_file", arguments={"file_path": str(target)})
            ]
        ),
        LLMResponse(content="file read done"),
    ])
    events = await _collect(engine, "read the file")

    starts = [e for e in events if isinstance(e, ToolExecutionStarted)]
    completed = [e for e in events if isinstance(e, ToolExecutionCompleted)]
    assert len(starts) == 1
    assert starts[0].tool_name == "read_file"
    assert starts[0].tool_input["file_path"] == str(target)
    assert len(completed) == 1
    assert completed[0].tool_name == "read_file"
    assert "line one" in completed[0].output
    assert completed[0].is_error is False

    turns = [e for e in events if isinstance(e, AssistantTurnComplete)]
    assert len(turns) == 1
    assert turns[0].message.text == "file read done"


@pytest.mark.asyncio
async def test_tool_error_is_reported(tmp_path):
    missing = tmp_path / "nope.txt"
    engine = _make_engine([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"file_path": str(missing)})]),
        LLMResponse(content="nothing found"),
    ])
    events = await _collect(engine, "read missing file")

    completed = [e for e in events if isinstance(e, ToolExecutionCompleted)]
    assert len(completed) == 1
    assert completed[0].is_error is True
    assert "not found" in completed[0].output


@pytest.mark.asyncio
async def test_llm_failure_emits_error_event():
    # ScriptedLLM raises when the script runs out of turns.
    engine = _make_engine([LLMResponse(content="first reply")])
    await _collect(engine, "turn one")

    events = await _collect(engine, "turn two that runs out of script")
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert "out of turns" in errors[0].message


@pytest.mark.asyncio
async def test_usage_is_tracked():
    engine = _make_engine([LLMResponse(content="some words here")])
    await _collect(engine, "hi")
    assert engine.total_usage.output_tokens > 0


@pytest.mark.asyncio
async def test_max_rounds_exhausted_ends_turn():
    # Never a plain-text reply: every round requests a tool call.
    script = [
        LLMResponse(tool_calls=[ToolCall(id=f"c{i}", name="glob", arguments={"pattern": "*.py"})])
        for i in range(3)
    ]
    engine = _make_engine(script, max_rounds=2)
    events = await _collect(engine, "loop forever")
    complete = [e for e in events if isinstance(e, AssistantTurnComplete)]
    assert len(complete) == 1
    assert "maximum tool-call rounds" in complete[0].message.text


def test_to_corecoder_messages_with_tool_blocks():
    messages = [
        ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="checking"),
                ToolUseBlock(id="call_1", name="glob", input={"pattern": "*.py"}),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="call_1", content="['a.py']", is_error=False)],
        ),
        ConversationMessage.from_user_text("next question"),
    ]
    converted = _to_corecoder_messages(messages)

    assert converted[0]["role"] == "assistant"
    assert converted[0]["content"] == "checking"
    assert converted[0]["tool_calls"][0]["function"]["name"] == "glob"
    assert converted[1]["role"] == "tool"
    assert converted[1]["tool_call_id"] == "call_1"
    assert converted[1]["content"] == "['a.py']"
    assert converted[2] == {"role": "user", "content": "next question"}


def test_load_messages_hydrates_agent():
    engine = _make_engine([])
    engine.load_messages([
        ConversationMessage.from_user_text("earlier question"),
        ConversationMessage(role="assistant", content=[TextBlock(text="earlier answer")]),
    ])
    assert len(engine.messages) == 2
    assert engine._agent.messages == [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]


def test_setters():
    engine = _make_engine([])
    engine.set_system_prompt("new prompt")
    assert engine.system_prompt == "new prompt"
    assert engine._agent._system == "new prompt"

    engine.set_model("new-model")
    assert engine.model == "new-model"
    assert engine._agent.llm.model == "new-model"

    engine.set_max_turns(3)
    assert engine.max_turns == 3
    assert engine._agent.max_rounds == 3

    engine.set_max_turns(None)
    assert engine.max_turns is None


def test_clear_resets_agent_and_usage():
    engine = _make_engine([LLMResponse(content="words")])
    asyncio.run(_collect(engine, "hi"))
    assert engine.total_usage.output_tokens > 0
    engine.clear()
    assert engine.messages == []
    assert engine._agent.messages == []
    assert engine.total_usage.output_tokens == 0
