"""Tests for ScriptedProvider and OpenAICompatProvider."""

from __future__ import annotations

import pytest

from javis.engines.corecoder.llm import (
    LLMRequest,
    LLMResponse,
    OpenAICompatProvider,
    ScriptedProvider,
)

# --- ScriptedProvider ---


@pytest.mark.asyncio
async def test_scripted_plays_back_turns():
    llm = ScriptedProvider([LLMResponse(content="first"), LLMResponse(content="second")])
    assert (await llm.achat(LLMRequest(messages=[]))).content == "first"
    assert (await llm.achat(LLMRequest(messages=[]))).content == "second"


@pytest.mark.asyncio
async def test_scripted_streams_through_on_token():
    seen = []
    llm = ScriptedProvider([LLMResponse(content="hello world")])
    resp = await llm.achat(LLMRequest(messages=[]), on_token=seen.append)
    assert resp.content == "hello world"
    assert seen == ["hello world"]


@pytest.mark.asyncio
async def test_scripted_out_of_turns_raises():
    llm = ScriptedProvider([])
    with pytest.raises(RuntimeError, match="ran out of turns"):
        await llm.achat(LLMRequest(messages=[]))


@pytest.mark.asyncio
async def test_scripted_counts_tokens_per_instance():
    llm = ScriptedProvider([LLMResponse(content="some words here")])
    assert llm.total_completion_tokens == 0
    await llm.achat(LLMRequest(messages=[]))
    assert llm.total_completion_tokens == 3


def test_scripted_sync_chat_works():
    llm = ScriptedProvider([LLMResponse(content="sync reply")])
    assert llm.chat(LLMRequest(messages=[])).content == "sync reply"


# --- aggregation (achat derived from achat_stream) ---


@pytest.mark.asyncio
async def test_achat_aggregates_stream_deltas():
    class DeltaProvider(ScriptedProvider):
        async def achat_stream(
            self,
            request,
            *,
            extra_body=None,
            on_token=None,
            on_reasoning=None,
        ):
            del request, extra_body
            yield LLMResponse(content="foo")
            yield LLMResponse(content="bar", tool_calls=[])

    llm = DeltaProvider([LLMResponse(content="ignored")])
    resp = await llm.achat(LLMRequest(messages=[]))
    assert resp.content == "foobar"


# --- LLMRequest / cache key / extra_body ---


def test_llm_request_field_defaults():
    """Sampling params default to None = use provider constructor default."""
    req = LLMRequest(messages=[{"role": "user", "content": "hi"}])
    assert req.tools is None
    assert req.max_tokens is None
    assert req.temperature is None
    assert req.stop is None
    assert req.top_p is None
    assert req.seed is None
    assert req.response_format is None


def test_cache_key_includes_sampling_params():
    """Cache key covers every input that can change the output."""
    llm = ScriptedProvider([])
    base = LLMRequest(messages=[{"role": "user", "content": "hi"}])
    k1 = llm._cache_key(base)
    # 采样参数变化 → key 变化（避免命中错误的缓存）
    assert llm._cache_key(LLMRequest(messages=base.messages, temperature=0.7)) != k1
    assert llm._cache_key(LLMRequest(messages=base.messages, seed=42)) != k1
    assert llm._cache_key(LLMRequest(messages=base.messages, stop=["STOP"])) != k1
    assert llm._cache_key(LLMRequest(messages=base.messages, response_format={"type": "json"})) != k1
    # 相同请求 → 相同 key
    assert llm._cache_key(LLMRequest(messages=base.messages)) == k1


def test_base_params_override_and_extra_body():
    """Request 非 None 字段覆盖构造默认；extra_body 合并进 SDK params。"""
    llm = OpenAICompatProvider(model="m", api_key="k", temperature=0.5, max_tokens=100)
    params = llm._base_params(LLMRequest(messages=[], temperature=0.9))
    assert params["temperature"] == 0.9  # request 覆盖构造默认
    assert params["max_tokens"] == 100  # None → 构造默认
    params2 = llm._base_params(LLMRequest(messages=[]), extra_body={"user": "u1"})
    assert params2["temperature"] == 0.5  # None → 构造默认
    assert params2["user"] == "u1"  # extra_body 合并
    params3 = llm._base_params(LLMRequest(messages=[], response_format={"type": "json_object"}))
    assert params3["response_format"] == {"type": "json_object"}


# --- OpenAICompatProvider construction ---


def test_openai_compat_constructs_with_empty_api_key(monkeypatch):
    """Empty api_key must not raise at construction (the SDK raises eagerly);
    the auth failure should surface at the first chat call instead."""
    for var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    llm = OpenAICompatProvider(model="x", api_key="")
    assert llm.api_key == ""
    assert llm._client is None  # lazy: client not built yet


def test_openai_compat_lazy_client_builds_on_first_call(monkeypatch):
    for var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    llm = OpenAICompatProvider(model="x", api_key="sk-test", base_url="http://localhost:1")
    client = llm._ensure_client()
    assert client is not None
    assert llm._client is client  # cached


def test_is_fallback_trigger_classification():
    from unittest.mock import MagicMock

    from openai import BadRequestError, RateLimitError

    from javis.engines.corecoder.llm import is_fallback_trigger

    response = MagicMock()
    response.request = MagicMock()

    assert is_fallback_trigger(RateLimitError(message="429", response=response, body=None)) is True
    assert is_fallback_trigger(BadRequestError(message="400", response=response, body=None)) is False
    assert is_fallback_trigger(ValueError("weird")) is True


def test_estimated_cost():
    from javis.engines.corecoder.llm import estimated_cost

    assert estimated_cost("deepseek-chat", 1_000_000, 0) == 0.27
    assert estimated_cost("no-such-model", 1, 1) is None


# --- streaming tool-call accumulation regression ---


def test_streaming_tool_call_accumulates_across_chunks():
    """Streaming tool calls span multiple chunks; id/name/args must survive."""
    import types

    from javis.engines.corecoder.llm import LLMResponse, _parse_delta

    def make_chunk(idx, tc_id=None, name=None, args=None):
        fn = None
        if name is not None or args is not None:
            fn = types.SimpleNamespace(name=name, arguments=args)
        return types.SimpleNamespace(
            usage=None,
            choices=[
                types.SimpleNamespace(
                    finish_reason=None,
                    delta=types.SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        tool_calls=[
                            types.SimpleNamespace(index=idx, id=tc_id, function=fn)
                        ],
                    ),
                )
            ],
        )

    tc_map: dict = {}
    merged = LLMResponse()
    for chunk in [
        make_chunk(0, tc_id="call_1", name="read_file"),
        make_chunk(0, args='{"file_path": "'),
        make_chunk(0, args='pyproject.toml"}'),
    ]:
        merged = merged.merge(_parse_delta(chunk, None, None, tc_map))

    assert len(merged.tool_calls) == 1
    tc = merged.tool_calls[0]
    assert tc.id == "call_1"
    assert tc.name == "read_file"
    assert tc.arguments == {"file_path": "pyproject.toml"}
