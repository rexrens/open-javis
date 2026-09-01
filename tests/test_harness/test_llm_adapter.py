"""Tests for ``JavisLLMAdapter`` — LLMProvider → core streaming protocol."""

from __future__ import annotations

import pytest

from javis.contracts.llm import LLMResponse, ToolCall
from javis.engines.harness.core.contracts import (
    GenerateOptions,
    LlmCallConfig,
    MaxTokensFinish,
    StopFinish,
    ToolCallBlock,
    ToolSchema,
    UserMessage,
)
from javis.engines.harness.llm_adapter import JavisLLMAdapter
from javis.engines.harness.providers import ScriptedProvider


def _options(**kwargs: object) -> GenerateOptions:
    base: dict[str, object] = {
        "provider": "scripted",
        "model": "scripted-demo",
        "messages": (UserMessage.from_text("hi"),),
        "tools": (ToolSchema(name="weather", description="w", parameters={}),),
    }
    base.update(kwargs)
    return GenerateOptions(**base)


async def _collect(adapter: JavisLLMAdapter, options: GenerateOptions) -> list[object]:
    return [chunk async for chunk in adapter.stream(options)]


@pytest.mark.asyncio
async def test_text_and_reasoning_deltas():
    provider = ScriptedProvider(
        [LLMResponse(content="hello", reasoning_content="think…", prompt_tokens=4, completion_tokens=2)]
    )
    chunks = await _collect(JavisLLMAdapter(provider), _options())

    types = [c.type for c in chunks]
    assert types == [
        "block-start", "text-delta",
        "block-start", "reasoning-delta",
        "usage", "finish",
        "block-end", "block-end",
    ]
    deltas = [c for c in chunks if c.type == "text-delta"]
    assert deltas[0].text == "hello"


@pytest.mark.asyncio
async def test_tool_call_blocks_and_finish():
    provider = ScriptedProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="t1", name="weather", arguments={"city": "Paris"})],
                finish_reason="tool_calls",
            )
        ]
    )
    chunks = await _collect(JavisLLMAdapter(provider), _options())

    starts = [c for c in chunks if c.type == "block-start"]
    assert [c.block_type for c in starts] == ["tool-call"]
    block_ends = [c for c in chunks if c.type == "block-end"]
    assert isinstance(block_ends[0].block, ToolCallBlock)
    assert block_ends[0].block.id == "t1"
    finish = next(c for c in chunks if c.type == "finish")
    from javis.engines.harness.core.contracts import ToolCallsFinish

    assert isinstance(finish.reason, ToolCallsFinish)


@pytest.mark.asyncio
async def test_max_tokens_finish_mapping():
    provider = ScriptedProvider([LLMResponse(content="truncated", finish_reason="length")])
    chunks = await _collect(JavisLLMAdapter(provider), _options())
    finish = next(c for c in chunks if c.type == "finish")
    assert isinstance(finish.reason, MaxTokensFinish)


@pytest.mark.asyncio
async def test_stop_finish_default():
    provider = ScriptedProvider([LLMResponse(content="done", finish_reason="stop")])
    chunks = await _collect(JavisLLMAdapter(provider), _options())
    finish = next(c for c in chunks if c.type == "finish")
    assert isinstance(finish.reason, StopFinish)


@pytest.mark.asyncio
async def test_prepare_call_surfaces_defaults():
    provider = ScriptedProvider([LLMResponse(content="x")])
    adapter = JavisLLMAdapter(provider)
    prepared = adapter.prepare_call(LlmCallConfig(provider="scripted", model="scripted-demo"))
    assert prepared.context == {"contextWindow": 128000}
    assert isinstance(prepared.stream, type(adapter.stream))


@pytest.mark.asyncio
async def test_usage_chunk_carries_tokens():
    provider = ScriptedProvider([LLMResponse(content="words here", prompt_tokens=11, completion_tokens=5)])
    chunks = await _collect(JavisLLMAdapter(provider), _options())
    usage = next(c for c in chunks if c.type == "usage")
    assert usage.usage.input_tokens == 11
    assert usage.usage.output_tokens == 5
