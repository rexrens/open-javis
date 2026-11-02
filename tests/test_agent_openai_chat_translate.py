import json

from javis.agent.model.openai.openai_chat import OpenAIChat
from javis.agent.model.request import Message, ModelRequest
from javis.agent.model.transport import ServerSentEvent
from javis.harness.types import (
    FinishChunk,
    MaxTokensFinish,
    ReasoningDeltaChunk,
    StopFinish,
    TextDeltaChunk,
    TokenUsage,
    ToolCallDeltaChunk,
    ToolCallsFinish,
    UsageChunk,
)


def _request(*messages: Message) -> ModelRequest:
    return ModelRequest(messages=list(messages))


def test_body_serializes_complete_multi_turn_history() -> None:
    model = OpenAIChat(model="test-model", api_key="test")
    request = ModelRequest(
        messages=[
            Message.system("be concise"),
            Message.user("hello"),
            Message.assistant("hi"),
            Message.user("what did I say?"),
        ],
        temperature=0.2,
        max_tokens=128,
        stop=["END"],
        extra={"top_p": 0.9},
    )

    body = model._body(request)

    assert body["messages"] == [
        {"role": "system", "content": "be concise"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "what did I say?"},
    ]
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 128
    assert body["stop"] == ["END"]
    assert body["top_p"] == 0.9
    assert body["stream"] is True


def test_headers_merge_custom_values_without_using_extra_body() -> None:
    model = OpenAIChat(
        model="test-model",
        api_key="test",
        headers={"X-Test": "yes"},
        extra_body={"top_p": 0.5},
    )

    headers = model._headers()

    assert headers["X-Test"] == "yes"
    assert "top_p" not in headers


def test_sync_invoke_stream_serializes_complete_history() -> None:
    model = OpenAIChat(model="test-model", api_key="test")
    request = ModelRequest(
        messages=[Message.user("hello"), Message.assistant("hi"), Message.user("again")]
    )
    request_body = {}

    class FakeTransport:
        def stream(self, _url, _headers, body):
            request_body.update(body)
            if False:
                yield

    model._transport = FakeTransport()

    list(model.invoke_stream(request))

    assert request_body["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "again"},
    ]


def test_translate_maps_openai_text_delta() -> None:
    model = OpenAIChat(model="test-model", api_key="test")
    chunk = ServerSentEvent(
        data=json.dumps(
            {
                "choices": [
                    {
                        "delta": {"content": "hello"},
                        "index": 0,
                        "finish_reason": None,
                    }
                ]
            }
        )
    )

    response = model._translate(chunk)

    assert response == TextDeltaChunk(index=0, text="hello")


def test_translate_maps_openai_finish_reason() -> None:
    model = OpenAIChat(model="test-model", api_key="test")
    chunk = ServerSentEvent(
        data=json.dumps(
            {
                "choices": [
                    {
                        "delta": {},
                        "index": 0,
                        "finish_reason": "length",
                    }
                ]
            }
        )
    )

    response = model._translate(chunk)

    assert response == FinishChunk(reason=MaxTokensFinish())


def test_translate_maps_openai_tool_call_finish_reason() -> None:
    model = OpenAIChat(model="test-model", api_key="test")
    chunk = ServerSentEvent(
        data=json.dumps(
            {
                "choices": [
                    {
                        "delta": {},
                        "index": 0,
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
    )

    response = model._translate(chunk)

    assert response == FinishChunk(reason=ToolCallsFinish())


def test_translate_maps_openai_reasoning_delta() -> None:
    model = OpenAIChat(model="test-model", api_key="test")
    chunk = ServerSentEvent(
        data=json.dumps(
            {
                "choices": [
                    {
                        "delta": {"reasoning_content": "thinking"},
                        "index": 0,
                        "finish_reason": None,
                    }
                ]
            }
        )
    )

    response = model._translate(chunk)

    assert response == ReasoningDeltaChunk(index=0, text="thinking")


def test_translate_maps_openai_tool_call_delta() -> None:
    model = OpenAIChat(model="test-model", api_key="test")
    chunk = ServerSentEvent(
        data=json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 2,
                                    "id": "call_123",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"query":',
                                    },
                                }
                            ]
                        },
                        "index": 0,
                        "finish_reason": None,
                    }
                ]
            }
        )
    )

    response = model._translate(chunk)

    assert response == ToolCallDeltaChunk(
        index=2,
        id="call_123",
        name="lookup",
        arguments_delta='{"query":',
    )


def test_translate_maps_openai_usage_with_cache_tokens() -> None:
    model = OpenAIChat(model="test-model", api_key="test")
    chunk = ServerSentEvent(
        data=json.dumps(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 40},
                    "completion_tokens_details": {"reasoning_tokens": 5},
                },
            }
        )
    )

    response = model._translate(chunk)

    assert response == UsageChunk(
        usage=TokenUsage(
            input_tokens=60,
            output_tokens=20,
            cache_read_tokens=40,
            reasoning_tokens=5,
        )
    )


async def test_ainvoke_stream_skips_done_marker() -> None:
    model = OpenAIChat(model="test-model", api_key="test")

    class FakeTransport:
        async def astream(self, *_args, **_kwargs):
            yield ServerSentEvent(
                data=json.dumps(
                    {
                        "choices": [
                            {
                                "delta": {"content": "hello"},
                                "index": 0,
                                "finish_reason": None,
                            }
                        ]
                    }
                )
            )
            yield ServerSentEvent(
                data=json.dumps(
                    {
                        "choices": [
                            {
                                "delta": {},
                                "index": 0,
                                "finish_reason": "stop",
                            }
                        ]
                    }
                )
            )
            yield ServerSentEvent(data="[DONE]")

    model._transport = FakeTransport()

    chunks = [chunk async for chunk in model.ainvoke_stream(_request(Message.user("hello")))]

    assert chunks == [
        TextDeltaChunk(index=0, text="hello"),
        FinishChunk(reason=StopFinish()),
    ]


async def test_ainvoke_stream_requests_server_sent_events() -> None:
    model = OpenAIChat(model="test-model", api_key="test")
    request_body = {}

    class FakeTransport:
        async def astream(self, _url, _headers, body):
            request_body.update(body)
            if False:
                yield

    model._transport = FakeTransport()

    [chunk async for chunk in model.ainvoke_stream(_request(Message.user("hello")))]

    assert request_body["stream"] is True
    assert request_body["messages"] == [{"role": "user", "content": "hello"}]
