"""Tests for Agent.chat() on_tool_result callback and public history API."""

from __future__ import annotations

import pytest

from corecoder.agent import Agent
from corecoder.llm import LLMResponse, ScriptedLLM, ToolCall


def _agent(script, **kwargs) -> Agent:
    return Agent(llm=ScriptedLLM(script=script), **kwargs)


def test_on_tool_result_single_call(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("content", encoding="utf-8")
    calls = []
    agent = _agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"file_path": str(target)})]),
        LLMResponse(content="done"),
    ])
    agent.chat("read", on_tool_result=lambda n, a, out, err: calls.append((n, a, out, err)))

    assert len(calls) == 1
    name, args, out, err = calls[0]
    assert name == "read_file"
    assert args == {"file_path": str(target)}
    assert "content" in out
    assert err is False


def test_on_tool_result_parallel(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    calls = []
    agent = _agent([
        LLMResponse(tool_calls=[
            ToolCall(id="c1", name="read_file", arguments={"file_path": str(a)}),
            ToolCall(id="c2", name="read_file", arguments={"file_path": str(b)}),
        ]),
        LLMResponse(content="done"),
    ])
    agent.chat("read both", on_tool_result=lambda n, a, out, err: calls.append((n, a, out, err)))

    assert len(calls) == 2
    assert {c[0] for c in calls} == {"read_file"}


def test_on_tool_result_reports_error():
    # read_file handles a missing file itself by returning an "Error: ... not
    # found" string (a successful execution per _exec_tool_with_status), so use
    # bad arguments instead: that is the path where is_error is True.
    calls = []
    agent = _agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={})]),
        LLMResponse(content="done"),
    ])
    agent.chat("read", on_tool_result=lambda n, a, out, err: calls.append((n, a, out, err)))

    assert len(calls) == 1
    assert calls[0][3] is True
    assert "bad arguments" in calls[0][2]


def test_on_tool_result_unknown_tool():
    calls = []
    agent = _agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="nope", arguments={})]),
        LLMResponse(content="done"),
    ])
    agent.chat("x", on_tool_result=lambda n, a, out, err: calls.append((n, a, out, err)))

    assert len(calls) == 1
    assert calls[0][0] == "nope"
    assert calls[0][3] is True


def test_load_messages_replaces_history():
    agent = _agent([LLMResponse(content="hi")])
    agent.chat("hello")
    assert len(agent.messages) == 2

    agent.load_messages([{"role": "user", "content": "restored"}])
    assert agent.messages == [{"role": "user", "content": "restored"}]


def test_set_system_prompt_updates_system():
    agent = _agent([LLMResponse(content="hi")])
    agent.set_system_prompt("custom system prompt")
    assert agent._system == "custom system prompt"
