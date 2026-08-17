"""Tests for Agent.achat() — the async chat loop."""

from __future__ import annotations

import asyncio

import pytest

from corecoder.agent import Agent
from corecoder.llm import AsyncScriptedLLM, LLMResponse, ToolCall


def _agent(script, **kwargs) -> Agent:
    return Agent(llm=AsyncScriptedLLM(script=script), **kwargs)


@pytest.mark.asyncio
async def test_achat_plain_text_reply():
    agent = _agent([LLMResponse(content="hello world")])
    reply = await agent.achat("hi")

    assert reply == "hello world"
    assert agent.messages[0] == {"role": "user", "content": "hi"}
    assert agent.messages[-1] == {"role": "assistant", "content": "hello world"}


@pytest.mark.asyncio
async def test_achat_tool_round_with_callbacks(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("line one", encoding="utf-8")
    calls = []
    agent = _agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"file_path": str(target)})]),
        LLMResponse(content="file read done"),
    ])
    reply = await agent.achat("read", on_tool_result=lambda n, a, out, err: calls.append((n, out, err)))

    assert reply == "file read done"
    assert len(calls) == 1
    assert calls[0][0] == "read_file"
    assert "line one" in calls[0][1]
    assert calls[0][2] is False
    assert agent.messages[2]["role"] == "tool"
    assert agent.messages[2]["tool_call_id"] == "c1"


@pytest.mark.asyncio
async def test_achat_parallel_tool_calls(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    agent = _agent([
        LLMResponse(tool_calls=[
            ToolCall(id="c1", name="read_file", arguments={"file_path": str(a)}),
            ToolCall(id="c2", name="read_file", arguments={"file_path": str(b)}),
        ]),
        LLMResponse(content="read both"),
    ])
    reply = await agent.achat("read both")

    assert reply == "read both"
    ids = [m["tool_call_id"] for m in agent.messages if m.get("role") == "tool"]
    assert ids == ["c1", "c2"]


@pytest.mark.asyncio
async def test_achat_out_of_turns_raises():
    agent = _agent([LLMResponse(content="first")])
    assert await agent.achat("one") == "first"
    with pytest.raises(RuntimeError, match="out of turns"):
        await agent.achat("two")


@pytest.mark.asyncio
async def test_achat_max_rounds_exhausted():
    script = [
        LLMResponse(tool_calls=[ToolCall(id=f"c{i}", name="glob", arguments={"pattern": "*.py"})])
        for i in range(3)
    ]
    agent = _agent(script, max_rounds=2)
    assert await agent.achat("loop") == "(reached maximum tool-call rounds)"


@pytest.mark.asyncio
async def test_achat_cancel_fixes_history(tmp_path):
    """Cancelling mid-tool-round must leave a valid history (every assistant
    tool_calls answered)."""
    agent = _agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "sleep 0.3"})]),
        LLMResponse(content="done"),
    ])
    task = asyncio.create_task(agent.achat("go"))
    await asyncio.sleep(0.05)  # let round 1 start: assistant msg appended, tool running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    answered = {m.get("tool_call_id") for m in agent.messages if m.get("role") == "tool"}
    assert "c1" in answered
    # assistant tool_calls must all be answered
    for m in agent.messages:
        for tc in m.get("tool_calls", []):
            assert tc["id"] in answered
