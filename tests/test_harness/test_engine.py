"""Tests for the ``HarnessEngine`` AgentEngine contract surface.

Covers what the old ``test_corecoder_engine.py`` did (initial state,
setters, restore, clear, usage across turns, ConversationMessage input,
tool metadata) against the harness engine.
"""

from __future__ import annotations

import pytest

from javis.contracts.messages import ConversationMessage
from javis.contracts.usage import UsageSnapshot
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



def _engine(script: list[object], **kwargs: object) -> HarnessEngine:
    return HarnessEngine(
        adapter=ScriptedAdapter(script=script),
        provider_name="scripted",
        model="scripted-demo",
        system_prompt="test prompt",
        cwd="/tmp",
        workspace="/tmp",
        session_id="sess",
        javis_tools=create_default_tool_registry(),
        **kwargs,
    )


async def _drain(engine: HarnessEngine, prompt: str) -> list[object]:
    return [event async for event in engine.submit_message(prompt)]


def test_initial_state():
    engine = _engine([_resp(content="x")])
    assert engine.messages == []
    assert engine.total_usage == UsageSnapshot()
    assert engine.model == "scripted-demo"
    assert engine.system_prompt == "test prompt"
    assert engine.max_turns is None
    assert isinstance(engine.tool_metadata, dict)


def test_setters():
    engine = _engine([_resp(content="x")])
    engine.set_model("other-model")
    assert engine.model == "other-model"
    assert engine._adapter.model == "other-model"
    engine.set_system_prompt("new prompt")
    assert engine.system_prompt == "new prompt"
    engine.set_max_turns(5)
    assert engine.max_turns == 5
    assert engine._loop_config.max_steps_per_turn == 5
    engine.set_max_turns(None)
    assert engine.max_turns is None
    engine.set_effort("high")
    assert engine._effort == "high"


@pytest.mark.asyncio
async def test_set_effort_is_written_to_next_request():
    engine = _engine([_resp(content="x")])
    engine.set_effort("high")
    await _drain(engine, "go")
    assert engine._session.request_header()["config"]["reasoningEffort"] == "high"


@pytest.mark.asyncio
async def test_load_messages_restores_history():
    engine = _engine(
        [_resp(content="restored and answered", prompt_tokens=3, completion_tokens=2)]
    )
    saved = [
        ConversationMessage.from_user_text("previous question"),
        ConversationMessage(role="assistant", content=[__import__("javis.contracts.messages", fromlist=["TextBlock"]).TextBlock(text="previous answer")]),
    ]
    engine.load_messages(saved)
    assert [m.text for m in engine.messages] == ["previous question", "previous answer"]

    events = await _drain(engine, "continue")
    assert any(getattr(e, "text", "") == "restored and answered" for e in events)


@pytest.mark.asyncio
async def test_clear_resets_inner_loop():
    engine = _engine([_resp(content="hi", prompt_tokens=1, completion_tokens=1)])
    await _drain(engine, "one")
    assert engine.total_usage.input_tokens == 1
    engine.clear()
    assert engine.messages == []
    assert engine.total_usage == UsageSnapshot()
    # inner session is fresh: next turn starts at 1
    assert engine._session.last_turn() == 0


@pytest.mark.asyncio
async def test_usage_accumulates_across_turns():
    engine = _engine(
        [
            _resp(content="first", prompt_tokens=10, completion_tokens=2),
            _resp(content="second", prompt_tokens=20, completion_tokens=4),
        ]
    )
    await _drain(engine, "one")
    await _drain(engine, "two")
    assert engine.total_usage.input_tokens == 30
    assert engine.total_usage.output_tokens == 6


@pytest.mark.asyncio
async def test_submit_message_with_conversation_message_object():
    engine = _engine([_resp(content="handled", prompt_tokens=2, completion_tokens=1)])
    message = ConversationMessage.from_user_text("as an object")
    events = [event async for event in engine.submit_message(message)]
    assert any(getattr(e, "text", "") == "handled" for e in events)
    assert engine.messages[0].text == "as an object"


@pytest.mark.asyncio
async def test_tool_metadata_is_mutable():
    engine = _engine([_resp(content="x")], tool_metadata={"permission_mode": "default"})
    assert engine.tool_metadata["permission_mode"] == "default"
    engine.tool_metadata["permission_mode"] = "acceptEdits"
    assert engine.tool_metadata["permission_mode"] == "acceptEdits"


@pytest.mark.asyncio
async def test_tool_call_round_through_engine(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("payload", encoding="utf-8")
    engine = _engine(
        [
            _resp(
                tool_calls=[_tc(id="c1", name="read_file", arguments={"file_path": str(target)})],
                finish_reason="tool_calls",
            ),
            _resp(content="done reading"),
        ]
    )
    events = await _drain(engine, "read it")
    from javis.contracts.types import AgentToolCallResult

    results = [e for e in events if isinstance(e, AgentToolCallResult)]
    assert results and "payload" in results[0].output
