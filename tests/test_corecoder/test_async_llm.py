"""Tests for AsyncScriptedLLM and AsyncLLM construction."""

from __future__ import annotations

import pytest

from corecoder.llm import AsyncLLM, AsyncScriptedLLM, LLMResponse


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


def test_async_llm_constructs_with_empty_api_key(monkeypatch):
    """Empty api_key must not raise at construction (the SDK raises eagerly);
    the auth failure should surface at the first chat call instead."""
    # httpx builds the proxy map from env at client construction; socks://
    # entries without the httpx[socks] extra would fail the construction.
    for var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    llm = AsyncLLM(model="x", api_key="")
    assert llm.client.api_key == "sk-missing"
