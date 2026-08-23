"""Tests for the CoreCoder agent loop (``corecoder.Agent``).

CoreCoder is the standalone reference agent; javis plugs agents in through
the ``AgentBackend`` protocol. These tests exercise the agent directly.
"""

from __future__ import annotations

import pytest

from javis.engines.corecoder.agent import Agent
from javis.engines.corecoder.llm import LLMResponse, ScriptedProvider, ToolCall
from javis.engines.corecoder.tools import all_tools


def _make_agent(script: list[LLMResponse], *, max_rounds: int = 10, tools=None) -> Agent:
    llm = ScriptedProvider(script=script)
    return Agent(llm=llm, tools=tools, max_rounds=max_rounds)


def test_plain_text_reply():
    agent = _make_agent([LLMResponse(content="hello world")])
    reply = agent.chat("hi")

    assert reply == "hello world"
    assert agent.messages[0] == {"role": "user", "content": "hi"}
    assert agent.messages[-1] == {"role": "assistant", "content": "hello world"}


def test_tool_call_round(tmp_path):
    target = tmp_path / "hello.txt"
    target.write_text("line one\nline two\n", encoding="utf-8")

    agent = _make_agent([
        LLMResponse(
            tool_calls=[
                ToolCall(id="call_1", name="read_file", arguments={"file_path": str(target)})
            ]
        ),
        LLMResponse(content="file read done"),
    ])
    reply = agent.chat("read the file")

    assert reply == "file read done"
    assert agent.messages[1]["tool_calls"][0]["function"]["name"] == "read_file"
    tool_reply = agent.messages[2]
    assert tool_reply["role"] == "tool"
    assert tool_reply["tool_call_id"] == "call_1"
    assert "line one" in tool_reply["content"]


def test_tool_error_is_reported(tmp_path):
    missing = tmp_path / "nope.txt"
    agent = _make_agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"file_path": str(missing)})]),
        LLMResponse(content="nothing found"),
    ])
    reply = agent.chat("read missing file")

    assert reply == "nothing found"
    tool_reply = agent.messages[2]
    assert "not found" in tool_reply["content"]


def test_unknown_tool_is_reported():
    agent = _make_agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="nope", arguments={})]),
        LLMResponse(content="done"),
    ])
    reply = agent.chat("call an unknown tool")

    assert reply == "done"
    tool_reply = agent.messages[2]
    assert "unknown tool 'nope'" in tool_reply["content"]


def test_parallel_tool_calls(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")

    agent = _make_agent([
        LLMResponse(tool_calls=[
            ToolCall(id="c1", name="read_file", arguments={"file_path": str(a)}),
            ToolCall(id="c2", name="read_file", arguments={"file_path": str(b)}),
        ]),
        LLMResponse(content="read both"),
    ])
    reply = agent.chat("read both files")

    assert reply == "read both"
    ids = [m["tool_call_id"] for m in agent.messages if m.get("role") == "tool"]
    assert ids == ["c1", "c2"]
    contents = [m["content"] for m in agent.messages if m.get("role") == "tool"]
    assert "alpha" in contents[0]
    assert "beta" in contents[1]


def test_llm_failure_raises():
    agent = _make_agent([LLMResponse(content="first reply")])
    assert agent.chat("turn one") == "first reply"

    with pytest.raises(RuntimeError, match="out of turns"):
        agent.chat("turn two that runs out of script")


def test_max_rounds_exhausted_ends_turn():
    # Never a plain-text reply: every round requests a tool call.
    script = [
        LLMResponse(tool_calls=[ToolCall(id=f"c{i}", name="glob", arguments={"pattern": "*.py"})])
        for i in range(3)
    ]
    agent = _make_agent(script, max_rounds=2)
    reply = agent.chat("loop forever")

    assert reply == "(reached maximum tool-call rounds)"


def test_usage_is_tracked():
    agent = _make_agent([LLMResponse(content="some words here")])
    agent.chat("hi")
    assert agent.llm.total_completion_tokens > 0


def test_reset_clears_history():
    agent = _make_agent([LLMResponse(content="hello world")])
    agent.chat("hi")
    assert agent.messages
    agent.reset()
    assert agent.messages == []


def test_default_tools_include_core_set():
    agent = _make_agent([LLMResponse(content="ok")])
    names = {t.name for t in agent.tools}
    assert {"read_file", "write_file", "edit_file", "bash", "glob", "grep"} <= names
    assert names == {t.name for t in all_tools()}
