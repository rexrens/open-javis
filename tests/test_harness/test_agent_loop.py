"""Tests for the harness agent loop (``HarnessEngine`` over ``ReactLoopAgent``).

Successor of the old ``corecoder.Agent`` loop tests: the javis side now
drives the dsh-style loop through ``HarnessEngine.submit_message`` (an
``AgentEvent`` stream) instead of ``agent.chat`` callbacks. The javis
conversation mirror (``engine.messages``) is the assertion surface.
"""

from __future__ import annotations

from typing import Any

import pytest

from javis.contracts.messages import ToolResultBlock
from javis.harness.engine import HarnessEngine
from javis.harness.llm import chunk_response
from javis.harness.types import (
    MaxTokensFinish,
    StopFinish,
    TokenUsage,
    ToolCallBlock,
    ToolCallsFinish,
)
from javis.llm import ScriptedAdapter
from javis.tools import create_default_tool_registry


def _tc(id: str, name: str, arguments: dict) -> ToolCallBlock:
    """Build a ToolCallBlock (arguments are a JSON string on the wire)."""
    import json as _json

    return ToolCallBlock(id=id, name=name, arguments=_json.dumps(arguments, ensure_ascii=False))


def _resp(
    content: str | None = None,
    tool_calls: list[ToolCallBlock] | None = None,
    reasoning: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    finish_reason: str = "stop",
) -> list:
    """One scripted model turn: a chunk sequence built via chunk_response."""
    finish = StopFinish()
    if finish_reason == "tool_calls":
        finish = ToolCallsFinish()
    elif finish_reason == "length":
        finish = MaxTokensFinish()
    usage = (
        TokenUsage(input_tokens=prompt_tokens, output_tokens=completion_tokens)
        if (prompt_tokens or completion_tokens)
        else None
    )
    return chunk_response(
        text=content,
        reasoning=reasoning,
        tool_calls=tool_calls or None,
        usage=usage,
        finish=finish,
    )



def _make_engine(script: list[object], **kwargs: Any) -> HarnessEngine:
    tools = create_default_tool_registry()
    return HarnessEngine(
        adapter=ScriptedAdapter(script=script),
        provider_name="scripted",
        model="scripted-demo",
        system_prompt="test",
        cwd="/tmp",
        workspace="/tmp",
        session_id="test-session",
        javis_tools=tools,
        **kwargs,
    )


async def _run(engine: HarnessEngine, prompt: str) -> list[Any]:
    return [event async for event in engine.submit_message(prompt)]


def _final_text(engine: HarnessEngine) -> str:
    return "".join(
        block.text
        for message in engine.messages
        if message.role == "assistant"
        for block in message.content
        if hasattr(block, "text")
    )


def _tool_results(engine: HarnessEngine) -> list[ToolResultBlock]:
    return [
        block
        for message in engine.messages
        for block in message.content
        if isinstance(block, ToolResultBlock)
    ]


@pytest.mark.asyncio
async def test_plain_text_reply():
    engine = _make_engine([_resp(content="hello world", prompt_tokens=3, completion_tokens=2)])
    events = await _run(engine, "hi")

    turn_end = events[-1]
    assert turn_end.text == "hello world"
    assert engine.messages[0].role == "user"
    assert engine.messages[0].text == "hi"
    assert "hello world" in _final_text(engine)


@pytest.mark.asyncio
async def test_tool_call_round(tmp_path):
    target = tmp_path / "hello.txt"
    target.write_text("line one\nline two\n", encoding="utf-8")

    engine = _make_engine([
        _resp(
            tool_calls=[_tc(id="call_1", name="read_file", arguments={"file_path": str(target)})],
            finish_reason="tool_calls",
        ),
        _resp(content="file read done"),
    ])
    await _run(engine, "read the file")

    results = _tool_results(engine)
    assert len(results) == 1
    assert results[0].tool_use_id == "call_1"
    assert "line one" in results[0].content


@pytest.mark.asyncio
async def test_tool_error_is_reported(tmp_path):
    missing = tmp_path / "nope.txt"
    engine = _make_engine([
        _resp(tool_calls=[_tc(id="c1", name="read_file", arguments={"file_path": str(missing)})], finish_reason="tool_calls"),
        _resp(content="nothing found"),
    ])
    await _run(engine, "read missing file")

    results = _tool_results(engine)
    # javis tools return error TEXT with is_error=False (successful execution)
    assert results and "not found" in results[0].content


@pytest.mark.asyncio
async def test_unknown_tool_is_reported():
    engine = _make_engine([
        _resp(tool_calls=[_tc(id="c1", name="nope", arguments={})], finish_reason="tool_calls"),
        _resp(content="done"),
    ])
    await _run(engine, "call an unknown tool")

    results = _tool_results(engine)
    assert results and results[0].is_error and "unknown tool 'nope'" in results[0].content


@pytest.mark.asyncio
async def test_parallel_tool_calls(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")

    engine = _make_engine([
        _resp(tool_calls=[
            _tc(id="c1", name="read_file", arguments={"file_path": str(a)}),
            _tc(id="c2", name="read_file", arguments={"file_path": str(b)}),
        ], finish_reason="tool_calls"),
        _resp(content="read both"),
    ])
    await _run(engine, "read both files")

    results = _tool_results(engine)
    assert [r.tool_use_id for r in results] == ["c1", "c2"]
    assert "alpha" in results[0].content
    assert "beta" in results[1].content


@pytest.mark.asyncio
async def test_llm_failure_yields_agent_error():
    engine = _make_engine([_resp(content="first reply")])
    await _run(engine, "turn one")

    events = await _run(engine, "turn two that runs out of script")
    from javis.contracts.types import AgentError

    assert any(isinstance(e, AgentError) for e in events)


@pytest.mark.asyncio
async def test_max_steps_guard_ends_turn_with_status():
    from javis.contracts.types import AgentStatus

    script = [
        _resp(
            tool_calls=[_tc(id=f"c{i}", name="glob", arguments={"pattern": "*.py"})],
            finish_reason="tool_calls",
        )
        for i in range(5)
    ]
    engine = _make_engine(script, max_steps_per_turn=2)
    events = await _run(engine, "loop forever")

    statuses = [e for e in events if isinstance(e, AgentStatus)]
    assert statuses and "max steps" in statuses[0].message
    # exactly two tool-call steps ran
    assert len(_tool_results(engine)) == 2


@pytest.mark.asyncio
async def test_usage_is_tracked():
    engine = _make_engine([_resp(content="some words here", prompt_tokens=7, completion_tokens=3)])
    await _run(engine, "hi")
    assert engine.total_usage.input_tokens == 7
    assert engine.total_usage.output_tokens == 3


@pytest.mark.asyncio
async def test_clear_resets_history():
    engine = _make_engine([_resp(content="hello world")])
    await _run(engine, "hi")
    assert engine.messages
    engine.clear()
    assert engine.messages == []
    assert engine.total_usage.total_tokens == 0


@pytest.mark.asyncio
async def test_default_tools_include_core_set():
    engine = _make_engine([_resp(content="ok")])
    names = {tool.name for tool in engine._core_tools.all()}
    assert {"read_file", "write_file", "edit_file", "bash", "glob", "grep", "agent"} <= names
