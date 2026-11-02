"""The model-call service: registry rules and terminal-chunk guarantees."""

from __future__ import annotations

from harness.llm import LlmAdapter, LlmError, LlmService
from harness.types import GenerateOptions, block_end, block_start, text_delta
from cordis_harness_support.stub import StubContext


def _options() -> GenerateOptions:
    return GenerateOptions(provider="fake", model="fake-model", messages=[])


class _Adapter(LlmAdapter):
    def __init__(self, chunks=None, error=None):
        self.chunks = chunks or []
        self.error = error

    async def stream(self, options):
        if self.error is not None:
            raise self.error
        for chunk in self.chunks:
            yield chunk


async def test_unknown_provider_yields_no_adapter_finish():
    service = LlmService(StubContext())
    chunks = [chunk async for chunk in service.stream(_options())]
    assert chunks == [
        {
            "type": "finish",
            "reason": {"kind": "error", "failure": {"code": "NO_ADAPTER", "message": 'no adapter registered for provider "fake"'}},
        }
    ]


async def test_registration_deduplicates_and_disposes():
    ctx = StubContext()
    service = LlmService(ctx)
    adapter = _Adapter()
    dispose = service.register_adapter(["fake", "fake-2"], adapter)
    assert service.list_providers() == ["fake", "fake-2"]
    assert service.get_adapter("fake") is adapter
    assert ctx.names().count("llm/adapters-updated") == 1

    try:
        service.register_adapter(["fake"], _Adapter())
    except LlmError as error:
        assert error.code == "DUPLICATE_ADAPTER"
    else:  # pragma: no cover - the duplicate must fail
        raise AssertionError("duplicate registration must fail")

    dispose()
    dispose()  # idempotent
    assert service.list_providers() == []
    assert ctx.names().count("llm/adapters-updated") == 2


async def test_adapter_without_terminal_chunk_still_terminates():
    service = LlmService(StubContext())
    service.register_adapter(["fake"], _Adapter([block_start(0, "text"), text_delta(0, "hi"), block_end(0, {"type": "text", "text": "hi"})]))
    chunks = [chunk async for chunk in service.stream(_options())]
    assert chunks[-1] == {"type": "finish", "reason": {"kind": "stop"}}


async def test_adapter_failures_become_error_finishes():
    service = LlmService(StubContext())
    service.register_adapter(["fake"], _Adapter(error=LlmError("RATE_LIMIT", "slow down", 429)))
    chunks = [chunk async for chunk in service.stream(_options())]
    assert chunks[-1]["reason"]["kind"] == "error"
    assert chunks[-1]["reason"]["failure"] == {"message": "slow down", "code": "RATE_LIMIT", "status": 429}

    service2 = LlmService(StubContext())
    service2.register_adapter(["fake"], _Adapter(error=RuntimeError("boom")))
    chunks = [chunk async for chunk in service2.stream(_options())]
    assert chunks[-1]["reason"]["failure"] == {"message": "RuntimeError: boom", "code": "ERROR"}


async def test_models_come_from_the_adapter():
    class _Catalog(_Adapter):
        def list_models(self, provider):
            return ["one", "two"]

    service = LlmService(StubContext())
    service.register_adapter(["fake"], _Catalog())
    assert service.list_models("fake") == ["one", "two"]
    assert service.list_models("missing") == []
