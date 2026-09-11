"""Tests for the ``Harness``'s Harness contract surface.

Covers what the old ``test_corecoder_engine.py`` did (initial state,
setters, restore, clear, usage across turns, ConversationMessage input,
tool metadata) against the harness shell.
"""

from __future__ import annotations

import pytest

from javis.contracts.messages import ConversationMessage
from javis.contracts.usage import UsageSnapshot
from javis.cordis import Context
from javis.harness.harness import Harness
from javis.harness.plugins.agent_loop import MutableLoopConfig
from javis.harness.stream import chunk_response
from javis.harness.types import (
    AgentLoopConfig,
    AgentLoopService,
    MaxTokensFinish,
    StopFinish,
    TokenUsage,
    ToolCallBlock,
    ToolCallsFinish,
)
from tests.test_harness.support import make_harness


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


async def _drain(engine: Harness, prompt: str) -> list[object]:
    return [event async for event in engine.submit_message(prompt)]


def test_initial_state():
    engine = make_harness([_resp(content="x")])
    assert engine.messages == []
    assert engine.total_usage == UsageSnapshot()
    assert engine.model == "scripted-demo"
    assert engine.system_prompt == "test prompt"
    assert engine.max_turns is None
    assert isinstance(engine.tool_metadata, dict)


def test_setters():
    engine = make_harness([_resp(content="x")])
    engine.set_model("other-model")
    assert engine.model == "other-model"
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
    engine = make_harness([_resp(content="x")])
    engine.set_effort("high")
    await _drain(engine, "go")
    assert engine._session.request_header()["config"]["reasoningEffort"] == "high"


@pytest.mark.asyncio
async def test_load_messages_restores_history():
    engine = make_harness(
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
    engine = make_harness([_resp(content="hi", prompt_tokens=1, completion_tokens=1)])
    await _drain(engine, "one")
    assert engine.total_usage.input_tokens == 1
    engine.clear()
    assert engine.messages == []
    assert engine.total_usage == UsageSnapshot()
    # inner session is fresh: next turn starts at 1
    assert engine._session.last_turn() == 0


@pytest.mark.asyncio
async def test_usage_accumulates_across_turns():
    engine = make_harness(
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
    engine = make_harness([_resp(content="handled", prompt_tokens=2, completion_tokens=1)])
    message = ConversationMessage.from_user_text("as an object")
    events = [event async for event in engine.submit_message(message)]
    assert any(getattr(e, "text", "") == "handled" for e in events)
    assert engine.messages[0].text == "as an object"


@pytest.mark.asyncio
async def test_tool_metadata_is_mutable():
    engine = make_harness([_resp(content="x")], tool_metadata={"permission_mode": "default"})
    assert engine.tool_metadata["permission_mode"] == "default"
    engine.tool_metadata["permission_mode"] = "acceptEdits"
    assert engine.tool_metadata["permission_mode"] == "acceptEdits"


@pytest.mark.asyncio
async def test_tool_call_round_through_harness(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("payload", encoding="utf-8")
    engine = make_harness(
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


@pytest.mark.asyncio
async def test_agent_tool_runs_a_sub_agent_end_to_end():
    """The ``agent`` tool's lazy factory resolves the harness and runs a
    second scripted turn inside a fresh sub-agent session."""
    from javis.contracts.types import AgentToolCallResult

    engine = make_harness(
        [
            _resp(
                tool_calls=[_tc(id="c1", name="agent", arguments={"task": "sub task"})],
                finish_reason="tool_calls",
            ),
            _resp(content="sub answer"),
            _resp(content="parent done"),
        ]
    )
    events = await _drain(engine, "delegate")

    results = [e for e in events if isinstance(e, AgentToolCallResult)]
    assert results and "sub answer" in results[0].output
    assert any(getattr(e, "text", "") == "parent done" for e in events)


def test_frozen_loop_config_is_copied_not_mutated():
    """A row may publish the frozen ``AgentLoopConfig``; ``Harness`` must copy
    its values into a mutable config instead of mutating it in place."""
    frozen = AgentLoopConfig(max_parallel_tool_calls=2, max_steps_per_turn=7)
    service = AgentLoopService(frozen)

    engine = make_harness([_resp(content="x")], loop_service=service)

    config = engine._loop_config
    assert isinstance(config, MutableLoopConfig)
    assert config is not frozen
    assert config.max_parallel_tool_calls == 2
    assert config.max_steps_per_turn == 7
    assert config.default_max_steps_per_turn == 7
    assert service.config is config
    assert frozen.max_steps_per_turn == 7  # never mutated

    engine.set_max_turns(5)
    assert config.max_steps_per_turn == 5
    assert frozen.max_steps_per_turn == 7

    engine.set_max_turns(None)
    assert config.max_steps_per_turn == 7
    assert frozen.max_steps_per_turn == 7


def test_remount_restores_the_row_default_not_the_last_override():
    """A re-mount (HMR) over the same ``agentLoop`` service restores the row's
    configured default, not the previous harness's ``set_max_turns`` value."""
    service = AgentLoopService(
        MutableLoopConfig(max_parallel_tool_calls=4, max_steps_per_turn=7)
    )
    first = make_harness([_resp(content="x")], loop_service=service)
    first.set_max_turns(5)

    second = make_harness([_resp(content="x")], loop_service=service)
    second.set_max_turns(None)

    assert second._loop_config.max_steps_per_turn == 7


def test_missing_required_services_raise_loud_error():
    """A composition that forgot the prompt/loop rows fails at construction
    with one error naming every missing service and the row that provides it."""
    with pytest.raises(RuntimeError) as excinfo:
        Harness(Context(), provider_name="scripted", model="m")

    message = str(excinfo.value)
    assert "systemPrompt" in message
    assert "javis.harness.plugins.system_prompt" in message
    assert "agentLoop" in message
    assert "javis.harness.plugins.agent_loop" in message
