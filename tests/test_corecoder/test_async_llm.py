"""Tests for AsyncScriptedLLM — the offline double for AsyncLLM."""

from __future__ import annotations

import pytest

from corecoder.llm import AsyncScriptedLLM, LLMResponse


@pytest.mark.asyncio
async def test_async_scripted_plays_back_turns():
    llm = AsyncScriptedLLM([LLMResponse(content="first"), LLMResponse(content="second")])
    assert (await llm.chat(messages=[])).content == "first"
    assert (await llm.chat(messages=[])).content == "second"


@pytest.mark.asyncio
async def test_async_scripted_streams_through_on_token():
    seen = []
    llm = AsyncScriptedLLM([LLMResponse(content="hello world")])
    resp = await llm.chat(messages=[], on_token=seen.append)
    assert resp.content == "hello world"
    assert seen == ["hello world"]


@pytest.mark.asyncio
async def test_async_scripted_out_of_turns_raises():
    llm = AsyncScriptedLLM([])
    with pytest.raises(RuntimeError, match="out of turns"):
        await llm.chat(messages=[])


@pytest.mark.asyncio
async def test_async_scripted_counts_tokens_per_instance():
    llm = AsyncScriptedLLM([LLMResponse(content="some words here")])
    assert llm.total_completion_tokens == 0
    await llm.chat(messages=[])
    assert llm.total_completion_tokens == 3
