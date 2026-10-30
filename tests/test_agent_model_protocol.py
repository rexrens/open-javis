"""Tests for the agent model protocol models."""

from javis.agent.model.proto import FinishReason, TokenUsage, ToolCallBlock
from javis.agent.model.request import Message, ModelRequest
from javis.agent.model.response import ModelResponse


def test_model_request_exposes_message_text() -> None:
    request = ModelRequest(
        messages=[
            Message.system("follow "),
            Message.user("instructions"),
        ],
        system="the rules",
    )

    assert request.all_system_text() == "follow the rules"
    assert [message.text for message in request.non_system_messages()] == ["instructions"]
    assert [message.text for message in request.all_messages()] == [
        "follow ",
        "instructions",
    ]


def test_model_request_accepts_legacy_message_keyword() -> None:
    request = ModelRequest(message=[Message.user("hello")])

    assert request.messages == [Message.user("hello")]
    assert request.message == request.messages


def test_token_usage_merge_and_response_helpers() -> None:
    usage = TokenUsage(input_tokens=2, output_tokens=3, cache_read_tokens=1)
    other = TokenUsage(input_tokens=4, output_tokens=5, cache_write_tokens=2)
    response = ModelResponse(
        blocks=[
            {"type": "text", "text": "hello"},
            {"type": "tool-call", "id": "call-1", "name": "lookup", "arguments": "{}"},
        ],
        finish_reason=FinishReason(kind="stop"),
    )

    assert usage.merge(other).billed_input_tokens == 9
    assert response.text == "hello"
    assert isinstance(response.tool_calls[0], ToolCallBlock)
