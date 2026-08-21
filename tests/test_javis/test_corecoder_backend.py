"""Tests for CoreCoderBackend — the AgentBackend adapter over corecoder.Agent."""

from __future__ import annotations

import asyncio

import pytest

from corecoder.agent import Agent
from corecoder.llm import LLMResponse, ScriptedProvider, ToolCall
from javis.contracts.types import (
    AgentError,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.engines.corecoder.backend import CoreCoderBackend, _to_corecoder_messages
from javis.contracts.messages import ConversationMessage, ImageBlock, TextBlock, ToolResultBlock


def _backend(script, **kwargs) -> CoreCoderBackend:
    llm = ScriptedProvider(script=script)
    agent = Agent(llm=llm)
    return CoreCoderBackend(agent, model="test-model", system_prompt="test system", **kwargs)


async def _collect(backend: CoreCoderBackend, prompt: str):
    return [e async for e in backend.run_turn(prompt, context=None)]  # context unused by backend


@pytest.mark.asyncio
async def test_plain_text_turn():
    backend = _backend([LLMResponse(content="hello world")])
    events = await _collect(backend, "hi")

    deltas = [e for e in events if isinstance(e, AgentTextDelta)]
    ends = [e for e in events if isinstance(e, AgentTurnEnd)]
    assert "".join(e.text for e in deltas) == "hello world"
    assert len(ends) == 1
    assert ends[0].text == "hello world"
    assert ends[0].usage is not None
    assert ends[0].usage.output_tokens == 2  # "hello world"


@pytest.mark.asyncio
async def test_tool_call_turn(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("line one\nline two\n", encoding="utf-8")
    backend = _backend([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"file_path": str(target)})]),
        LLMResponse(content="file read done"),
    ])
    events = await _collect(backend, "read the file")

    starts = [e for e in events if isinstance(e, AgentToolCallStart)]
    results = [e for e in events if isinstance(e, AgentToolCallResult)]
    assert len(starts) == 1
    assert starts[0].tool_name == "read_file"
    assert starts[0].tool_input == {"file_path": str(target)}
    assert len(results) == 1
    assert results[0].tool_name == "read_file"
    assert "line one" in results[0].output
    assert results[0].is_error is False

    ends = [e for e in events if isinstance(e, AgentTurnEnd)]
    assert len(ends) == 1
    assert ends[0].text == "file read done"


@pytest.mark.asyncio
async def test_llm_failure_yields_error_event():
    backend = _backend([LLMResponse(content="first")])
    await _collect(backend, "one")

    events = await _collect(backend, "two")  # script exhausted -> RuntimeError
    errors = [e for e in events if isinstance(e, AgentError)]
    assert len(errors) == 1
    assert "out of turns" in errors[0].message
    assert not any(isinstance(e, AgentTurnEnd) for e in events)


class _GatedLLM:
    """Async LLM double: first call returns a tool call, second parks on a gate
    until released, so the producer is provably mid-turn when cancelled."""

    def __init__(self, first: LLMResponse, gate: asyncio.Event, final: LLMResponse):
        self._first = first
        self._gate = gate
        self._final = final
        self._used = False
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    async def achat(self, request, *, extra_body=None, on_token=None, on_reasoning=None):
        del extra_body, on_token, on_reasoning
        if not self._used:
            self._used = True
            return self._first
        await self._gate.wait()
        return self._final


@pytest.mark.asyncio
async def test_cancellation_ends_without_turn_end_and_history_stays_valid(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("content", encoding="utf-8")
    gate = asyncio.Event()
    llm = _GatedLLM(
        first=LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"file_path": str(target)})]),
        gate=gate,
        final=LLMResponse(content="done"),
    )
    backend = CoreCoderBackend(Agent(llm=llm), model="test-model", system_prompt="test system")

    seen: list = []

    async def consume():
        async for event in backend.run_turn("read the file", context=None):
            seen.append(event)

    task = asyncio.create_task(consume())
    while not any(isinstance(e, AgentToolCallResult) for e in seen):
        await asyncio.sleep(0)  # producer is now parked on the gate
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not any(isinstance(e, (AgentTurnEnd, AgentError)) for e in seen)
    tool_replies = {m["tool_call_id"] for m in backend.agent.messages if m.get("role") == "tool"}
    tool_call_ids = [
        tc["id"] for m in backend.agent.messages if m.get("tool_calls") for tc in m["tool_calls"]
    ]
    assert tool_call_ids
    assert set(tool_call_ids) <= tool_replies


def test_load_history_converts_messages():
    backend = _backend([])
    backend.load_history([
        ConversationMessage.from_user_text("question"),
        ConversationMessage(role="assistant", content=[TextBlock(text="answer")]),
    ])
    assert backend.agent.messages == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]


def test_clear_history_resets_agent():
    # The sync chat() path requires a sync provider (ScriptedProvider.chat returns
    # a coroutine, so agent.chat would raise AttributeError).
    from corecoder.llm import ScriptedProvider

    llm = ScriptedProvider(script=[LLMResponse(content="hi")])
    agent = Agent(llm=llm)
    backend = CoreCoderBackend(agent, model="test-model", system_prompt="test system")
    backend.agent.chat("hello")  # sync path still works
    assert backend.agent.messages
    backend.clear_history()
    assert backend.agent.messages == []


def test_to_corecoder_messages_handles_images_and_tool_results():
    messages = [
        ConversationMessage(role="user", content=[
            TextBlock(text="look at this"),
            ImageBlock(media_type="image/png", data="AAAA", source_path="/x.png"),
        ]),
        ConversationMessage(role="user", content=[
            ToolResultBlock(tool_use_id="call_1", content="['a.py']", is_error=False),
        ]),
        ConversationMessage(role="assistant", content=[
            TextBlock(text="checking"),
        ]),
    ]
    converted = _to_corecoder_messages(messages)

    assert converted[0]["role"] == "user"
    assert "[image omitted" in converted[0]["content"]
    assert converted[1] == {"role": "tool", "tool_call_id": "call_1", "content": "['a.py']"}
    assert converted[2] == {"role": "assistant", "content": "checking"}


def test_build_corecoder_backend_applies_config(monkeypatch):
    from javis.engines.corecoder.backend import build_corecoder_backend

    # AsyncOpenAI builds an httpx client eagerly from proxy env vars; this
    # machine sets ALL_PROXY=socks://... which httpx rejects. Neutralize the
    # proxy env so the test is hermetic.
    for var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy",
                "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)

    backend = build_corecoder_backend(
        model="deepseek-chat",
        system_prompt="sp",
        cwd="/tmp",
        max_turns=12,
        engine_config={"api_key": "k", "max_tokens": 2048, "temperature": 0.2},
    )
    assert isinstance(backend, CoreCoderBackend)
    assert backend.model == "deepseek-chat"
    assert backend.agent.max_rounds == 12
    assert backend.agent._system == "sp"
    llm = backend.agent.llm
    assert llm.max_tokens == 2048
    assert llm.temperature == 0.2
