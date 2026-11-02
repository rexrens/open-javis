"""The OpenAI-compatible adapter: wire translation, streaming and failures."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx2
import openai
import pytest
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta, ChoiceDeltaToolCall, ChoiceDeltaToolCallFunction

from harness.llm import LlmService
from harness.llm_openai import (
    OpenAiAdapter,
    OpenAiSettings,
    map_error,
    map_finish_reason,
    to_wire_messages,
    to_wire_tools,
    to_wire_usage,
)
from harness.types import GenerateOptions, Message, ToolSchema, assistant_message, tool_call_block, tool_message, user_message
from cordis_harness_support.stub import StubContext


def _chunk(delta: ChoiceDelta | None, finish_reason: str | None = None, usage: Any = None) -> ChatCompletionChunk:
    choices = [Choice(index=0, delta=delta, finish_reason=finish_reason)] if delta is not None else []
    return ChatCompletionChunk(
        id="chunk",
        choices=choices,
        created=0,
        model="deepseek-v4-flash",
        object="chat.completion.chunk",
        usage=usage,
    )


class FakeStream:
    """An async-iterable stand-in for the SDK's ``AsyncStream``."""

    def __init__(self, chunks: list[Any], delay: float = 0.0):
        self.chunks = chunks
        self.delay = delay
        self.closed = False

    async def _iterate(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    def __aiter__(self):
        return self._iterate()

    async def close(self) -> None:
        self.closed = True


class StubAdapter(OpenAiAdapter):
    """Replaces the wire call, recording every request it was asked to send."""

    def __init__(self, settings: OpenAiSettings | None = None, chunks: list[Any] | None = None, error: BaseException | None = None, delay: float = 0.0):
        super().__init__(settings or OpenAiSettings())
        self.chunks = chunks or []
        self.error = error
        self.delay = delay
        self.requests: list[dict[str, Any]] = []

    async def _create(self, client, request):  # type: ignore[override]
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return FakeStream(self.chunks, self.delay)


def _options(**overrides: Any) -> GenerateOptions:
    options = GenerateOptions(
        provider="openai",
        model="deepseek-v4-flash",
        messages=[user_message("hello")],
        tools=[ToolSchema("bash", "run a command", {"type": "object", "properties": {"command": {"type": "string"}}})],
    )
    for key, value in overrides.items():
        setattr(options, key, value)
    return options


async def _collect(adapter: OpenAiAdapter, options: GenerateOptions) -> list[dict[str, Any]]:
    service = LlmService(StubContext())
    service.register_adapter(["openai"], adapter)
    return [chunk async for chunk in service.stream(options)]


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


# -- wire translation -------------------------------------------------------


def test_wire_messages_project_assistant_tool_calls_and_results():
    messages: list[Message] = [
        user_message("list files"),
        assistant_message([tool_call_block("call-1", "bash", '{"command":"ls"}')]),
        tool_message("call-1", "a.txt", is_error=True),
        assistant_message([{"type": "text", "text": "one file"}]),
        assistant_message([{"type": "reasoning", "text": "thought"}]),
        user_message(""),
    ]
    wire = to_wire_messages(messages)
    assert wire[0] == {"role": "user", "content": "list files"}
    assert wire[1]["tool_calls"] == [
        {"id": "call-1", "type": "function", "function": {"name": "bash", "arguments": '{"command":"ls"}'}}
    ]
    assert wire[2] == {"role": "tool", "tool_call_id": "call-1", "content": "error: a.txt"}
    assert wire[3] == {"role": "assistant", "content": "one file"}
    # A reasoning-only assistant message carries nothing for the provider.
    assert len(wire) == 4


def test_wire_tools_and_usage_translation():
    wire = to_wire_tools([ToolSchema("bash", "run it", {"type": "object"})])
    assert wire[0]["function"]["name"] == "bash"
    usage = CompletionUsage(prompt_tokens=10, completion_tokens=4, total_tokens=14)
    assert to_wire_usage(usage) == {"inputTokens": 10, "outputTokens": 4, "totalTokens": 14}
    assert to_wire_usage(None) is None
    assert map_finish_reason("stop", False) == "stop"
    assert map_finish_reason("tool_calls", True) == "tool-calls"
    assert map_finish_reason("length", False) == "max-tokens"
    assert map_finish_reason(None, True) == "tool-calls"


def test_error_mapping_is_stable():
    request = httpx2.Request("POST", "http://127.0.0.1:1/v1/chat/completions")
    response = httpx2.Response(401, request=request)
    assert map_error(openai.AuthenticationError("nope", response=response, body=None)).code == "AUTH"
    assert map_error(openai.APIConnectionError(request=request)).code == "CONNECTION"
    assert map_error(RuntimeError("boom")).code == "ERROR"


# -- streaming --------------------------------------------------------------


async def test_text_stream_and_request_shape():
    usage = CompletionUsage(prompt_tokens=5, completion_tokens=2, total_tokens=7)
    chunks = [
        _chunk(ChoiceDelta(content="he")),
        _chunk(ChoiceDelta(content="llo")),
        _chunk(ChoiceDelta(content=""), finish_reason="stop", usage=usage),
    ]
    adapter = StubAdapter(chunks=chunks)
    streamed = await _collect(adapter, _options())

    kinds = [chunk["type"] for chunk in streamed]
    assert kinds[0] == "block-start"
    assert [chunk["text"] for chunk in streamed if chunk["type"] == "text-delta"] == ["he", "llo"]
    assert streamed[-2] == {"type": "usage", "usage": {"inputTokens": 5, "outputTokens": 2, "totalTokens": 7}}
    assert streamed[-1] == {"type": "finish", "reason": {"kind": "stop"}}
    assert streamed[-3]["block"] == {"type": "text", "text": "hello"}

    request = adapter.requests[0]
    assert request["model"] == "deepseek-v4-flash"
    assert request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}
    assert request["messages"] == [{"role": "user", "content": "hello"}]
    assert request["tools"][0]["function"]["name"] == "bash"
    assert request["tool_choice"] == "auto"


async def test_tool_call_deltas_accumulate_into_one_block():
    chunks = [
        _chunk(
            ChoiceDelta(
                tool_calls=[
                    ChoiceDeltaToolCall(
                        index=0,
                        id="call-1",
                        type="function",
                        function=ChoiceDeltaToolCallFunction(name="bash", arguments='{"command":'),
                    )
                ]
            )
        ),
        _chunk(
            ChoiceDelta(
                tool_calls=[
                    ChoiceDeltaToolCall(index=0, function=ChoiceDeltaToolCallFunction(arguments='"ls"}'))
                ]
            )
        ),
        _chunk(ChoiceDelta(), finish_reason="tool_calls"),
    ]
    streamed = await _collect(StubAdapter(chunks=chunks), _options())
    deltas = [chunk for chunk in streamed if chunk["type"] == "tool-call-delta"]
    assert [delta["argumentsDelta"] for delta in deltas] == ['{"command":', '"ls"}']
    assert all(delta["id"] == "call-1" for delta in deltas)
    assert streamed[-2]["block"] == {
        "type": "tool-call",
        "id": "call-1",
        "name": "bash",
        "arguments": '{"command":"ls"}',
    }
    assert streamed[-1]["reason"]["kind"] == "tool-calls"


async def test_reasoning_content_becomes_a_reasoning_block():
    delta = ChoiceDelta.model_construct(content=None, reasoning_content="thinking")
    chunks = [_chunk(delta), _chunk(ChoiceDelta(content="answer")), _chunk(ChoiceDelta(), finish_reason="stop")]
    streamed = await _collect(StubAdapter(chunks=chunks), _options())
    reasoning = [chunk for chunk in streamed if chunk["type"] == "reasoning-delta"]
    assert reasoning == [{"type": "reasoning-delta", "index": 0, "text": "thinking"}]
    assert {"type": "block-end", "index": 0, "block": {"type": "reasoning", "text": "thinking"}} in streamed
    assert {"type": "block-end", "index": 1, "block": {"type": "text", "text": "answer"}} in streamed


# -- failures ---------------------------------------------------------------


async def test_endpoint_that_rejects_stream_options_is_retried_without_it():
    request = httpx2.Request("POST", "http://127.0.0.1:1/v1/chat/completions")
    response = httpx2.Response(400, request=request)
    error = openai.BadRequestError(
        "unknown field: stream_options",
        response=response,
        body=None,
    )

    class Retrying(StubAdapter):
        async def _create(self, client, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise error
            return FakeStream([_chunk(ChoiceDelta(content="ok")), _chunk(ChoiceDelta(), finish_reason="stop")])

    adapter = Retrying()
    streamed = await _collect(adapter, _options())
    assert len(adapter.requests) == 2
    assert "stream_options" in adapter.requests[0]
    assert "stream_options" not in adapter.requests[1]
    assert streamed[-1]["reason"]["kind"] == "stop"


async def test_provider_errors_become_error_finishes():
    request = httpx2.Request("POST", "http://127.0.0.1:1/v1/chat/completions")
    response = httpx2.Response(401, request=request)
    adapter = StubAdapter(error=openai.AuthenticationError("bad key", response=response, body=None))
    streamed = await _collect(adapter, _options())
    assert streamed[-1]["reason"]["kind"] == "error"
    assert streamed[-1]["reason"]["failure"]["code"] == "AUTH"


async def test_missing_credential_never_calls_the_provider(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    adapter = StubAdapter()
    streamed = await _collect(adapter, _options())
    assert adapter.requests == []
    assert streamed == [
        {
            "type": "finish",
            "reason": {
                "kind": "error",
                "failure": {
                    "message": "no API key found; set one of: OPENAI_API_KEY, DEEPSEEK_API_KEY",
                    "code": "MISSING_CREDENTIAL",
                },
            },
        }
    ]


async def test_deepseek_api_key_is_used_as_a_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    adapter = StubAdapter(chunks=[_chunk(ChoiceDelta(content="ok")), _chunk(ChoiceDelta(), finish_reason="stop")])
    streamed = await _collect(adapter, _options())
    assert streamed[-1]["reason"]["kind"] == "stop"


async def test_cancellation_yields_an_aborted_finish():
    adapter = StubAdapter(chunks=[_chunk(ChoiceDelta(content="partial"))], delay=5)
    service = LlmService(StubContext())
    service.register_adapter(["openai"], adapter)
    seen: list[dict[str, Any]] = []

    async def consume():
        async for chunk in service.stream(_options()):
            seen.append(chunk)

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen[-1]["type"] == "finish"
    assert seen[-1]["reason"]["kind"] == "aborted"
