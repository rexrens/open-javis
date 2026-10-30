import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

from javis.agent.model.transport import ServerSentEvent
from javis.harness.types import (
    FinishChunk,
    MaxTokensFinish,
    ReasoningDeltaChunk,
    StopFinish,
    StreamChunk,
    TextDeltaChunk,
    TokenUsage,
    ToolCallDeltaChunk,
    ToolCallsFinish,
    UsageChunk,
)

from ..model import Model
from ..request import Message, ModelRequest
from ..response import ModelResponse


class OpenAIChat(Model):
    provider = "openai"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 3,
        max_retries: int = 3,
        headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(model, api_key, base_url, timeout, max_retries, headers, extra_body)

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.headers:
            headers.update(self.headers)

        return headers

    def _resolve_instruction_role(self) -> str:
        if self.provider == "openai" and self._is_o1_or_newer(self.model):
            return "developer"
        return "system"

    def _is_o1_or_newer(self, model: str) -> bool:
        model = (model or "").lower()
        return model.startswith(("o1", "o3", "o4"))

    def _format_message(self, message: Message) -> dict[str, str]:
        role = self._resolve_instruction_role() if message.role == "system" else message.role
        return {"role": role, "content": message.text}

    def _request_messages(self, request: ModelRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append(
                {
                    "role": self._resolve_instruction_role(),
                    "content": request.system,
                }
            )
        messages.extend(self._format_message(message) for message in request.messages)
        return messages

    def _body(self, request: ModelRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._request_messages(request),
            "stream": True,
        }

        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.stop is not None:
            body["stop"] = request.stop
        if request.response_format is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_format.name,
                    "schema": request.response_format.json_schema,
                    "strict": True,
                },
            }

        body.update(self.extra_body)
        body.update(request.extra)
        return body

    def invoke(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    async def ainvoke(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    def invoke_stream(self, request: ModelRequest) -> Iterator[StreamChunk]:
        for sse in self._transport.stream(
            self._url(),
            self._headers(),
            self._body(request),
        ):
            translated = self._translate(sse)
            if translated is not None:
                yield translated

    def _translate(self, chunk: ServerSentEvent) -> StreamChunk | None:
        if not chunk.data or chunk.data == "[DONE]":
            return None

        payload = json.loads(chunk.data)

        # The usage parts
        usage = payload.get("usage")
        if usage:
            prompt_tokens = usage.get("prompt_tokens") or 0
            prompt_details = usage.get("prompt_tokens_details") or {}
            cached_tokens = prompt_details.get("cached_tokens") or 0
            completion_details = usage.get("completion_tokens_details") or {}
            return UsageChunk(
                usage=TokenUsage(
                    input_tokens=max(prompt_tokens - cached_tokens, 0),
                    output_tokens=usage.get("completion_tokens") or 0,
                    cache_read_tokens=cached_tokens or None,
                    reasoning_tokens=completion_details.get("reasoning_tokens"),
                )
            )

        choices = payload.get("choices") or []
        if not choices:
            return None

        choice = choices[0]
        delta = choice.get("delta") or {}
        choice_index = int(choice.get("index") or 0)

        if delta.get("content"):
            return TextDeltaChunk(index=choice_index, text=delta["content"])

        # OpenAI 源生为reasoning, DeepSeek等叫reasoning_content
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            return ReasoningDeltaChunk(index=choice_index, text=reasoning)

        tool_calls = delta.get("tool_calls") or []
        if tool_calls:
            tool_call = tool_calls[0]
            function = tool_call.get("function") or {}
            return ToolCallDeltaChunk(
                index=int(tool_call.get("index") or choice_index),
                id=tool_call.get("id") or "",
                name=function.get("name"),
                arguments_delta=function.get("arguments") or "",
            )

        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            return FinishChunk(reason=MaxTokensFinish())
        if finish_reason == "tool_calls":
            return FinishChunk(reason=ToolCallsFinish())
        if finish_reason:
            return FinishChunk(reason=StopFinish())

        return None

    async def ainvoke_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        async for sse in self._transport.astream(
            self._url(),
            self._headers(),
            self._body(request),
        ):
            translated = self._translate(sse)
            if translated is not None:
                yield translated
