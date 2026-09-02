"""Tests for the dsh-style LLM layer: ScriptedAdapter + LlmRuntime.

Successor of the old ``JavisLLMAdapter`` tests (LLMProvider → core streaming
protocol): the two-layer bridge is gone; adapters emit ``StreamChunk``
directly and ``LlmRuntime`` (adapter registry + ``llm/stream`` waterfall +
``prepare_call``) is the service the agent loop consumes.
"""

from __future__ import annotations

import pytest

from javis.cordis import Context
from javis.harness.llm import chunk_response
from javis.harness.types import (
    ErrorFinish,
    GenerateOptions,
    LlmCallConfig,
    LlmError,
    MaxTokensFinish,
    StopFinish,
    ToolCallBlock,
    ToolSchema,
    UserMessage,
)
from javis.llm import (
    LlmConfigurableProvider,
    LlmDiscoveredModel,
    LlmModelDiscoveryRequest,
    LlmRuntime,
    ScriptedAdapter,
)

from . import test_async_llm as async_llm


def _options(**kwargs: object) -> GenerateOptions:
    base: dict[str, object] = {
        "provider": "scripted",
        "model": "scripted-demo",
        "messages": (UserMessage.from_text("hi"),),
        "tools": (ToolSchema(name="weather", description="w", parameters={}),),
    }
    base.update(kwargs)
    return GenerateOptions(**base)


async def _collect(stream: object) -> list[object]:
    return [chunk async for chunk in stream]


def _runtime(script: list[list[object]], *, provider: str = "scripted") -> tuple[LlmRuntime, ScriptedAdapter]:
    ctx = Context()
    runtime = LlmRuntime(ctx)
    adapter = ScriptedAdapter(script, model="scripted-demo")
    runtime.register_adapter([provider], adapter)
    return runtime, adapter


@pytest.mark.asyncio
async def test_scripted_text_and_reasoning_deltas():
    runtime, _ = _runtime(
        [chunk_response(text="hello", reasoning="think…", usage=async_llm._tu(4, 2))]
    )
    chunks = await _collect(runtime.stream(_options()))

    types = [c.type for c in chunks]
    assert types == [
        "block-start", "reasoning-delta", "block-end",
        "block-start", "text-delta", "block-end",
        "usage", "finish",
    ]
    deltas = [c for c in chunks if c.type == "text-delta"]
    assert deltas[0].text == "hello"


@pytest.mark.asyncio
async def test_scripted_tool_call_blocks_and_finish():
    runtime, _ = _runtime(
        [
            chunk_response(
                tool_calls=[
                    ToolCallBlock(id="t1", name="weather", arguments='{"city": "Paris"}')
                ],
                finish=async_llm._tf("tool_calls"),
            )
        ]
    )
    chunks = await _collect(runtime.stream(_options()))

    starts = [c for c in chunks if c.type == "block-start"]
    assert [c.block_type for c in starts] == ["tool-call"]
    block_ends = [c for c in chunks if c.type == "block-end"]
    assert isinstance(block_ends[0].block, ToolCallBlock)
    assert block_ends[0].block.id == "t1"
    finish = next(c for c in chunks if c.type == "finish")
    from javis.harness.types import ToolCallsFinish

    assert isinstance(finish.reason, ToolCallsFinish)


@pytest.mark.asyncio
async def test_scripted_max_tokens_finish():
    runtime, _ = _runtime([chunk_response(text="truncated", finish=MaxTokensFinish())])
    finish = next(c for c in await _collect(runtime.stream(_options())) if c.type == "finish")
    assert isinstance(finish.reason, MaxTokensFinish)


@pytest.mark.asyncio
async def test_scripted_stop_finish_default():
    runtime, _ = _runtime([chunk_response(text="done")])
    finish = next(c for c in await _collect(runtime.stream(_options())) if c.type == "finish")
    assert isinstance(finish.reason, StopFinish)


@pytest.mark.asyncio
async def test_scripted_out_of_turns_normalized_to_error_finish():
    """An adapter throw becomes a terminal ``error`` finish (dsh semantics)."""
    runtime, _ = _runtime([])
    chunks = await _collect(runtime.stream(_options()))
    finish = next(c for c in chunks if c.type == "finish")
    assert isinstance(finish.reason, ErrorFinish)
    assert finish.reason.failure.code == "UNKNOWN"


# ---------------------------------------------------------------------------
# LlmRuntime registry + prepare_call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_lists_providers_and_rejects_duplicates():
    ctx = Context()
    runtime = LlmRuntime(ctx)
    adapter = ScriptedAdapter([])
    runtime.register_adapter(["mock", "other"], adapter)
    assert [p.id for p in runtime.list_providers()] == ["mock", "other"]
    with pytest.raises(LlmError, match="already registered"):
        runtime.register_adapter(["mock"], ScriptedAdapter([]))
    with pytest.raises(LlmError, match="no adapter registered"):
        await runtime.resolve_model_info("missing", "x")


@pytest.mark.asyncio
async def test_register_adapter_handle_replace_swaps_routes():
    ctx = Context()
    runtime = LlmRuntime(ctx)
    first = ScriptedAdapter([])
    second = ScriptedAdapter([])
    handle = runtime.register_adapter(["mock"], first)
    handle.replace(["mock", "extra"])
    assert [p.id for p in runtime.list_providers()] == ["mock", "extra"]
    handle.replace(["mock"])  # atomic swap back
    assert [p.id for p in runtime.list_providers()] == ["mock"]
    # the same adapter instance is kept
    assert runtime._adapters["mock"].adapter is first
    del second


@pytest.mark.asyncio
async def test_configurable_provider_registrations_release_independently():
    """Disposing one directory registration must not remove another's routes."""
    ctx = Context()
    runtime = LlmRuntime(ctx)
    first = runtime.register_configurable_providers(
        [LlmConfigurableProvider(provider="a", display_name="A", settings_ns="ns")]
    )
    second = runtime.register_configurable_providers(
        [LlmConfigurableProvider(provider="b", display_name="B", settings_ns="ns")]
    )

    assert [entry.provider for entry in runtime.list_configurable_providers()] == ["a", "b"]
    await first()
    assert [entry.provider for entry in runtime.list_configurable_providers()] == ["b"]
    await second()
    assert runtime.list_configurable_providers() == []


@pytest.mark.asyncio
async def test_configurable_provider_directory_disposes_multiple_entries_cleanly():
    """Multiple entries from one handle should be removable without iteration errors."""
    ctx = Context()
    runtime = LlmRuntime(ctx)
    handle = runtime.register_configurable_providers(
        [
            LlmConfigurableProvider(provider="a", display_name="A", settings_ns="ns"),
            LlmConfigurableProvider(provider="b", display_name="B", settings_ns="ns"),
        ]
    )

    await handle()
    assert runtime.list_configurable_providers() == []


@pytest.mark.asyncio
async def test_configurable_provider_replace_does_not_leak_old_route():
    """``replace`` should swap the held route, not accumulate the old name."""
    ctx = Context()
    runtime = LlmRuntime(ctx)
    handle = runtime.register_configurable_providers(
        [LlmConfigurableProvider(provider="a", display_name="A", settings_ns="ns")]
    )
    handle.replace(
        [LlmConfigurableProvider(provider="b", display_name="B", settings_ns="ns")]
    )

    assert [entry.provider for entry in runtime.list_configurable_providers()] == ["b"]


@pytest.mark.asyncio
async def test_model_discovery_registers_discovers_and_disposes():
    """Model discovery should participate in the owning fiber's effects."""
    ctx = Context()
    runtime = LlmRuntime(ctx)

    async def discover(request):
        del request
        return [LlmDiscoveredModel(id="m1", name="Model One")]

    disposer = runtime.register_model_discovery("ns", discover)
    models = await runtime.discover_models(
        "ns", LlmModelDiscoveryRequest(provider="mock")
    )
    assert [model.id for model in models] == ["m1"]
    await disposer()
    with pytest.raises(LlmError, match="no model discovery"):
        await runtime.discover_models("ns", LlmModelDiscoveryRequest(provider="mock"))


@pytest.mark.asyncio
async def test_prepared_dispatch_bypasses_llm_stream_waterfall():
    """Prepared dispatch is bound to the registration and skips raw-stream waterfall."""
    runtime, _ = _runtime([chunk_response(text="hello")])
    seen: list[str] = []

    def on_stream(payload, next):
        seen.append(payload.model)
        return next()

    runtime.ctx.on("llm/stream", on_stream)
    prepared = await runtime.prepare_call(
        LlmCallConfig(provider="scripted", model="scripted-demo")
    )
    chunks = await _collect(prepared.stream(_options(max_tokens=4096)))
    assert seen == []
    assert any(c.type == "text-delta" for c in chunks)


@pytest.mark.asyncio
async def test_prepare_call_surfaces_adapter_defaults_and_context():
    runtime, _ = _runtime([chunk_response(text="x")])
    prepared = await runtime.prepare_call(LlmCallConfig(provider="scripted", model="scripted-demo"))
    assert prepared.adapter_defaults == {"maxTokens": True}
    assert prepared.context == {"contextWindow": 128000}
    assert prepared.config.max_tokens == 4096  # materialized from the adapter
    assert prepared.retry_policy is None


@pytest.mark.asyncio
async def test_prepared_call_dispatch_once_and_config_guard():
    runtime, _ = _runtime([chunk_response(text="x")])
    prepared = await runtime.prepare_call(LlmCallConfig(provider="scripted", model="scripted-demo"))
    stream = prepared.stream(_options(max_tokens=4096))
    await _collect(stream)
    # dispatch-once: a second dispatch of the same prepared call rejects
    with pytest.raises(LlmError, match="only be dispatched once"):
        await _collect(prepared.stream(_options(max_tokens=4096)))
    # config drift between prepare and dispatch rejects
    prepared2 = await runtime.prepare_call(LlmCallConfig(provider="scripted", model="scripted-demo"))
    with pytest.raises(LlmError, match="config changed"):
        await _collect(prepared2.stream(_options(max_tokens=9999)))


@pytest.mark.asyncio
async def test_adapter_failure_normalized_to_error_finish():
    """An adapter throw becomes a terminal ``error`` finish chunk (no raise)."""
    ctx = Context()
    runtime = LlmRuntime(ctx)

    class BoomAdapter(ScriptedAdapter):
        async def stream(self, options: GenerateOptions):
            del options
            if False:
                yield None
            raise LlmError("connection reset", "TRANSIENT")

    runtime.register_adapter(["boom"], BoomAdapter([]))
    chunks = await _collect(
        runtime.stream(_options(provider="boom", model="scripted-demo"))
    )
    finish = next(c for c in chunks if c.type == "finish")
    assert isinstance(finish.reason, ErrorFinish)
    assert finish.reason.failure.code == "TRANSIENT"


@pytest.mark.asyncio
async def test_llm_stream_waterfall_intercepts():
    """Listeners can observe/wrap the stream via the ``llm/stream`` waterfall."""
    runtime, _ = _runtime([chunk_response(text="hello")])
    seen: list[str] = []

    def on_stream(payload, next):
        seen.append(payload.model)
        return next()

    runtime.ctx.on("llm/stream", on_stream)
    chunks = await _collect(runtime.stream(_options()))
    assert seen == ["scripted-demo"]
    assert any(c.type == "text-delta" for c in chunks)
