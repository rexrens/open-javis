from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from javis.agent.model.base import ModelAuthenticationError, ModelProviderError
from javis.agent.model.openai.openai_chat import OpenAIChat
from javis.contracts.messages import (
    ConversationMessage,
    ImageBlock,
    TextBlock,
)


def _response(
    *,
    content: str | None = "hello",
    finish_reason: str = "stop",
    usage: Any = None,
) -> Any:
    message = SimpleNamespace(
        role="assistant",
        content=content,
        reasoning_content=None,
        audio=None,
        refusal=None,
    )
    return SimpleNamespace(
        id="completion-1",
        model="test-model",
        created=123,
        system_fingerprint="fp",
        choices=[
            SimpleNamespace(
                index=0,
                message=message,
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


def _chunk(
    *,
    content: str | None = None,
    reasoning: str | None = None,
    finish_reason: str | None = None,
    usage: Any = None,
) -> Any:
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
    )
    return SimpleNamespace(
        id="chunk-1",
        model="test-model",
        created=123,
        system_fingerprint="fp",
        choices=[SimpleNamespace(index=0, delta=delta, finish_reason=finish_reason)]
        if finish_reason is not None or content is not None or reasoning is not None
        else [],
        usage=usage,
    )


class _Completions:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return self.result(**kwargs) if callable(self.result) else self.result


class _AsyncCompletions(_Completions):
    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return self.result(**kwargs) if callable(self.result) else self.result


class _Client:
    def __init__(self, result: Any, *, async_client: bool = False) -> None:
        self.completions = _AsyncCompletions(result) if async_client else _Completions(result)
        self.chat = SimpleNamespace(completions=self.completions)

    def is_closed(self) -> bool:
        return False


class _AsyncStream:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self.chunks:
            yield chunk


@dataclass
class _AssistantMessage:
    role: str | None = None
    content: Any = None
    parsed: Any = None
    reasoning_content: str | None = None
    provider_data: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    metrics: Any = None
    time_to_first_token: float | None = None


class _Answer(BaseModel):
    answer: str


def test_invoke_matches_signature_and_serializes_structured_output():
    usage = SimpleNamespace(
        prompt_tokens=11,
        completion_tokens=4,
        total_tokens=15,
        prompt_tokens_details=SimpleNamespace(cached_tokens=3),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=2),
    )
    client = _Client(_response(content='{"answer":"done"}', usage=usage))
    model = OpenAIChat(api_key="test", client=client)
    assistant = _AssistantMessage()

    result = model.invoke(
        [{"role": "user", "content": "hello"}],
        assistant,
        response_format=_Answer,
        temperature=0.2,
    )

    assert result.content == '{"answer":"done"}'
    assert result.parsed == _Answer(answer="done")
    assert result.block_type == "message"
    assert result.input_tokens == 11
    assert result.output_tokens == 4
    assert result.cache_read_tokens == 3
    assert result.reasoning_tokens == 2
    assert assistant.content == '{"answer":"done"}'
    assert assistant.parsed == _Answer(answer="done")

    request = client.completions.requests[0]
    assert request["messages"] == [{"role": "user", "content": "hello"}]
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["name"] == "_Answer"
    assert request["temperature"] == 0.2
    assert "tools" not in request
    assert "tool_choice" not in request
    assert "tools" not in inspect.signature(OpenAIChat.invoke).parameters
    assert "tool_choice" not in inspect.signature(OpenAIChat.ainvoke_stream).parameters


def test_contract_messages_expand_text_and_images():
    model = OpenAIChat(api_key="test")
    messages = [
        ConversationMessage.from_user_text("inspect"),
        ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="checking"),
            ],
        ),
    ]

    wire = model._format_all_messages(messages)

    assert wire[0] == {"role": "user", "content": "inspect"}
    assert wire[1] == {"role": "assistant", "content": "checking"}

    image_wire = model._format_all_messages(
        [
            ConversationMessage(
                role="user",
                content=[
                    TextBlock(text="caption"),
                    ImageBlock(media_type="image/png", data="YWJj"),
                ],
            )
        ]
    )
    assert image_wire[0]["content"][1]["image_url"]["url"] == ("data:image/png;base64,YWJj")


def test_structured_output_is_validated_against_response_signature():
    model = OpenAIChat(
        api_key="test",
        client=_Client(_response(content='{"wrong":"shape"}')),
    )
    with pytest.raises(ModelProviderError, match="response_format"):
        model.invoke(
            [{"role": "user", "content": "hi"}],
            response_format=_Answer,
        )


def test_invoke_stream_collects_text_reasoning_and_usage():
    usage = SimpleNamespace(
        prompt_tokens=7,
        completion_tokens=3,
        total_tokens=10,
        prompt_tokens_details=None,
        completion_tokens_details=None,
    )
    chunks = [
        _chunk(reasoning="thinking"),
        _chunk(content="answer"),
        _chunk(finish_reason="stop"),
        _chunk(usage=usage),
    ]
    model = OpenAIChat(api_key="test", client=_Client(iter(chunks)))
    assistant = _AssistantMessage()

    received = list(model.invoke_stream([{"role": "user", "content": "hi"}], assistant))

    assert [item.block_type for item in received] == [
        "reasoning_delta",
        "content_delta",
        "finish",
        "usage",
    ]
    assert assistant.content == "answer"
    assert assistant.reasoning_content == "thinking"
    assert assistant.input_tokens == 7
    assert assistant.output_tokens == 3
    assert assistant.total_tokens == 10
    assert assistant.time_to_first_token is not None


@pytest.mark.asyncio
async def test_async_invoke_and_stream():
    model = OpenAIChat(api_key="test", client=_Client(_response(content="async")))
    model.async_client = _Client(_response(content="async"), async_client=True)
    result = await model.ainvoke([{"role": "user", "content": "hi"}])
    assert result.content == "async"

    model.async_client = _Client(
        _AsyncStream([_chunk(content="a"), _chunk(content="b"), _chunk(finish_reason="stop")]),
        async_client=True,
    )
    chunks = [chunk async for chunk in model.ainvoke_stream([{"role": "user", "content": "hi"}])]
    assert [chunk.content for chunk in chunks if chunk.block_type == "content_delta"] == [
        "a",
        "b",
    ]
    assert chunks[-1].block_type == "finish"


def test_missing_api_key_raises_authentication_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model = OpenAIChat()
    with pytest.raises(ModelAuthenticationError):
        model.invoke([{"role": "user", "content": "hi"}])


def test_stream_options_fallback():
    calls = 0

    def create(**kwargs: Any):
        nonlocal calls
        calls += 1
        if calls == 1:
            from openai import BadRequestError

            request = httpx.Request("POST", "http://localhost/v1/chat/completions")
            response = httpx.Response(400, request=request)
            raise BadRequestError(
                "unknown field: stream_options",
                response=response,
                body=None,
            )
        return iter([_chunk(content="ok"), _chunk(finish_reason="stop")])

    model = OpenAIChat(api_key="test", client=_Client(create))
    chunks = list(model.invoke_stream([{"role": "user", "content": "hi"}]))
    assert calls == 2
    assert chunks[-1].block_type == "finish"
