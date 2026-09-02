"""Tests for the LLM layer: ScriptedAdapter / OpenAICompatAdapter / pricing.

Successor of the old ``ScriptedProvider`` / ``OpenAICompatProvider`` tests:
adapters emit ``StreamChunk`` directly (no ``LLMRequest``/``LLMResponse``
intermediate model).
"""

from __future__ import annotations

import pytest

from javis.harness.llm import chunk_response
from javis.harness.types import (
    FinishReason,
    MaxTokensFinish,
    StopFinish,
    TokenUsage,
    ToolCallBlock,
    ToolCallsFinish,
)
from javis.llm import OpenAICompatAdapter, ScriptedAdapter, estimated_cost, is_fallback_trigger


def _tu(input_tokens: int, output_tokens: int) -> TokenUsage:
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _tf(kind: str) -> FinishReason:
    if kind == "tool_calls":
        return ToolCallsFinish()
    if kind == "length":
        return MaxTokensFinish()
    return StopFinish()


# --- ScriptedAdapter ---


@pytest.mark.asyncio
async def test_scripted_plays_back_turns():
    adapter = ScriptedAdapter(
        [chunk_response(text="first"), chunk_response(text="second")]
    )
    first = [c async for c in adapter.stream(_opts())]
    second = [c async for c in adapter.stream(_opts())]
    assert [c.text for c in first if c.type == "text-delta"] == ["first"]
    assert [c.text for c in second if c.type == "text-delta"] == ["second"]


@pytest.mark.asyncio
async def test_scripted_out_of_turns_raises():
    adapter = ScriptedAdapter([])
    with pytest.raises(RuntimeError, match="ran out of turns"):
        [c async for c in adapter.stream(_opts())]


# --- OpenAICompatAdapter construction ---


def test_openai_compat_constructs_with_empty_api_key(monkeypatch):
    """Empty api_key must not raise at construction (the SDK raises eagerly);
    the auth failure should surface at the first chat call instead."""
    for var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    adapter = OpenAICompatAdapter(model="x", api_key="")
    assert adapter.api_key == ""
    assert adapter._aclient is None  # lazy: client not built yet


def test_openai_compat_lazy_aclient_builds_on_first_call(monkeypatch):
    for var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    adapter = OpenAICompatAdapter(model="x", api_key="sk-test", base_url="http://localhost:1")
    client = adapter._ensure_aclient()
    assert client is not None
    assert adapter._aclient is client  # cached


def test_openai_compat_resolve_model_advertises_defaults():
    adapter = OpenAICompatAdapter(model="deepseek-chat", api_key="k", max_tokens=2048, max_context_tokens=65536)

    async def go():
        info = await adapter.resolve_model("deepseek", "deepseek-chat")
        return info

    import asyncio

    info = asyncio.run(go())
    assert info.id == "deepseek-chat"
    assert info.context_window == 65536
    assert info.default_max_tokens == 2048


def test_is_fallback_trigger_classification():
    from unittest.mock import MagicMock

    from openai import BadRequestError, RateLimitError

    response = MagicMock()
    response.request = MagicMock()

    assert is_fallback_trigger(RateLimitError(message="429", response=response, body=None)) is True
    assert is_fallback_trigger(BadRequestError(message="400", response=response, body=None)) is False
    assert is_fallback_trigger(ValueError("weird")) is True


def test_estimated_cost():
    assert estimated_cost("deepseek-v4-flash", 1_000_000, 0) == 0.14
    assert estimated_cost("no-such-model", 1, 1) is None
    # the retired deepseek-chat/reasoner routes are no longer priced
    assert estimated_cost("deepseek-chat", 1_000_000, 0) is None


# --- streaming tool-call accumulation regression ---


def test_openai_chunk_tool_call_accumulates_across_chunks():
    """Streaming tool calls span multiple chunks; id/name/args must survive."""
    import types

    from javis.llm.openai_compat import _parse_openai_chunk, _tool_call_snapshots

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
    for chunk in [
        make_chunk(0, tc_id="call_1", name="read_file"),
        make_chunk(0, args='{"file_path": "'),
        make_chunk(0, args='pyproject.toml"}'),
    ]:
        _parse_openai_chunk(chunk, tc_map)

    snapshots = _tool_call_snapshots(tc_map)
    assert len(snapshots) == 1
    tc_id, name, arguments = snapshots[0]
    assert tc_id == "call_1"
    assert name == "read_file"
    assert arguments == '{"file_path": "pyproject.toml"}'



@pytest.mark.asyncio
async def test_openai_compat_stream_tool_call_accumulates_across_chunks():
    """The adapter must not emit an empty tool call before the arguments finish."""
    import json
    import types

    def make_chunk(idx, tc_id=None, name=None, args=None, finish_reason=None):
        fn = None
        if name is not None or args is not None:
            fn = types.SimpleNamespace(name=name, arguments=args)
        return types.SimpleNamespace(
            usage=None,
            choices=[
                types.SimpleNamespace(
                    finish_reason=finish_reason,
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

    chunks = [
        make_chunk(0, tc_id="call_1", name="read_file"),
        make_chunk(0, args='{"file_path": "'),
        make_chunk(0, args='pyproject.toml"}', finish_reason="tool_calls"),
    ]

    async def create(**params):
        del params

        async def stream():
            for chunk in chunks:
                yield chunk

        return stream()

    fake = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)
        )
    )
    adapter = OpenAICompatAdapter(model="deepseek-chat", api_key="k")
    adapter._aclient = fake

    emitted = [c async for c in adapter.stream(_opts())]
    block_ends = [c for c in emitted if c.type == "block-end" and isinstance(c.block, ToolCallBlock)]
    assert len(block_ends) == 1
    assert json.loads(block_ends[0].block.arguments) == {"file_path": "pyproject.toml"}


# --- package re-exports ---


def test_llm_package_reexports_adapter_surface() -> None:
    """``javis.llm`` re-exports the adapter/runtime/pricing surface."""
    import javis.llm as llm_pkg

    for name in (
        "LlmRuntime",
        "LLMAdapter",
        "OpenAICompatAdapter",
        "ScriptedAdapter",
        "estimated_cost",
        "is_fallback_trigger",
    ):
        assert getattr(llm_pkg, name, None) is not None, f"javis.llm missing {name}"


def _opts():
    from javis.harness.types import GenerateOptions, UserMessage

    return GenerateOptions(
        provider="scripted",
        model="scripted-demo",
        messages=(UserMessage.from_text("hi"),),
    )
