"""OpenAI Chat Completions model adapter.

The adapter keeps the public surface of the original ``OpenAIChat`` class while
normalizing the provider response into :class:`ModelResponse`. It supports
sync/async calls, streaming, structured output and usage metrics.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict, dataclass, field, is_dataclass
from os import getenv
from time import monotonic
from typing import Any, ClassVar, Literal, NoReturn, cast

import httpx
from pydantic import BaseModel

from javis.agent.model.base import (
    ContextWindowExceededError,
    Model,
    ModelAuthenticationError,
    ModelProviderError,
    ModelResponse,
)

try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        AuthenticationError,
        BadRequestError,
        RateLimitError,
    )
    from openai import (
        AsyncOpenAI as AsyncOpenAIClient,
    )
    from openai import (
        OpenAI as OpenAIClient,
    )
    from openai.types import CompletionUsage
    from openai.types.chat import ChatCompletion, ChatCompletionChunk
    from openai.types.chat.chat_completion_chunk import ChoiceDelta
except (ImportError, ModuleNotFoundError) as exc:
    raise ImportError("`openai` not installed. Please install using `pip install openai`") from exc


logger = logging.getLogger(__name__)


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    if is_dataclass(value):
        return asdict(cast(Any, value))
    if hasattr(value, "model_dump"):
        return cast(dict[str, Any], value.model_dump(exclude_none=True))
    if hasattr(value, "to_dict"):
        return cast(dict[str, Any], value.to_dict())
    if isinstance(value, dict):
        return dict(value)
    return {
        key: val for key, val in vars(value).items() if not key.startswith("_") and val is not None
    }


def _first_attr(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _without_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


@dataclass
class OpenAIChat(Model):
    """OpenAI-compatible Chat Completions client."""

    DEFAULT_ROLE_MAP: ClassVar[dict[str, str]] = {
        "system": "developer",
        "user": "user",
        "assistant": "assistant",
        "model": "assistant",
    }

    id: str = "gpt-5.4-mini"
    name: str = "OpenAIChat"
    provider: str = "OpenAI"
    supports_native_structured_outputs: bool = True

    store: bool | None = None
    reasoning_effort: str | None = None
    verbosity: Literal["low", "medium", "high"] | None = None
    metadata: dict[str, Any] | None = None
    frequency_penalty: float | None = None
    logit_bias: Any | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    modalities: list[str] | None = None
    audio: dict[str, Any] | None = None
    presence_penalty: float | None = None
    seed: int | None = None
    stop: str | list[str] | None = None
    temperature: float | None = None
    user: str | None = None
    top_p: float | None = None
    service_tier: str | None = None
    strict_output: bool = True
    extra_headers: dict[str, Any] | None = None
    extra_query: dict[str, Any] | None = None
    extra_body: dict[str, Any] | None = None
    request_params: dict[str, Any] = field(default_factory=dict)
    role_map: dict[str, str] = field(default_factory=dict)

    api_key: str | None = None
    organization: str | None = None
    base_url: str | httpx.URL | None = None
    timeout: float | None = None
    max_retries: int | None = None
    default_headers: dict[str, Any] | None = None
    default_query: dict[str, Any] | None = None
    http_client: httpx.Client | httpx.AsyncClient | None = None
    client_params: dict[str, Any] = field(default_factory=dict)
    client: OpenAIClient | None = field(default=None, repr=False)
    async_client: AsyncOpenAIClient | None = field(default=None, repr=False)

    def get_provider(self) -> str:
        return f"{self.provider} Chat"

    def _get_client_params(self) -> dict[str, Any]:
        if not self.api_key:
            self.api_key = getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ModelAuthenticationError(
                "OPENAI_API_KEY not set. Please set the OPENAI_API_KEY environment variable.",
                model_name=self.name,
                model_id=self.id,
            )

        base_params = {
            "api_key": self.api_key,
            "organization": self.organization,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "default_headers": self.default_headers,
            "default_query": self.default_query,
        }
        params = _without_none(base_params)
        params.update(self.client_params)
        return params

    @staticmethod
    def _is_closed(client: Any) -> bool:
        checker = getattr(client, "is_closed", None)
        return bool(checker()) if callable(checker) else False

    def get_client(self) -> OpenAIClient:
        if self.client is not None and not self._is_closed(self.client):
            return self.client

        params = self._get_client_params()
        if self.http_client is not None:
            if isinstance(self.http_client, httpx.Client):
                params["http_client"] = self.http_client
            else:
                logger.warning(
                    "Ignoring async http_client for synchronous OpenAI client %s",
                    self.id,
                )
        self.client = OpenAIClient(**params)
        return self.client

    def get_async_client(self) -> AsyncOpenAIClient:
        if self.async_client is not None and not self._is_closed(self.async_client):
            return self.async_client

        params = self._get_client_params()
        if self.http_client is not None:
            if isinstance(self.http_client, httpx.AsyncClient):
                params["http_client"] = self.http_client
            else:
                logger.warning(
                    "Ignoring sync http_client for asynchronous OpenAI client %s",
                    self.id,
                )
        self.async_client = AsyncOpenAIClient(**params)
        return self.async_client

    def get_request_params(
        self,
        response_format: dict[str, Any] | type[BaseModel] | None = None,
        run_response: Any = None,
    ) -> dict[str, Any]:
        del run_response
        params: dict[str, Any] = _without_none(
            {
                "store": self.store,
                "reasoning_effort": self.reasoning_effort,
                "verbosity": self.verbosity,
                "frequency_penalty": self.frequency_penalty,
                "logit_bias": self.logit_bias,
                "logprobs": self.logprobs,
                "top_logprobs": self.top_logprobs,
                "max_tokens": self.max_tokens,
                "max_completion_tokens": self.max_completion_tokens,
                "modalities": self.modalities,
                "audio": self.audio,
                "presence_penalty": self.presence_penalty,
                "seed": self.seed,
                "stop": self.stop,
                "temperature": self.temperature,
                "user": self.user,
                "top_p": self.top_p,
                "extra_headers": self.extra_headers,
                "extra_query": self.extra_query,
                "extra_body": self.extra_body,
                "metadata": self.metadata,
                "service_tier": self.service_tier,
            }
        )

        if response_format is not None:
            if isinstance(response_format, type) and issubclass(response_format, BaseModel):
                params["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": response_format.__name__,
                        "schema": response_format.model_json_schema(),
                        "strict": self.strict_output,
                    },
                }
            else:
                params["response_format"] = response_format

        params.update(self.request_params)
        return params

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("api_key", None)
        data.pop("client", None)
        data.pop("async_client", None)
        data.pop("http_client", None)
        return _without_none(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OpenAIChat:
        return cls(**data)

    def _map_role(self, role: str) -> str:
        return {**self.DEFAULT_ROLE_MAP, **self.role_map}.get(role, role)

    @staticmethod
    def _image_part(image: Any) -> dict[str, Any] | None:
        if isinstance(image, str):
            return {"type": "image_url", "image_url": {"url": image}}
        data = _model_dump(image) if not isinstance(image, dict) else dict(image)
        image_url = data.get("image_url") or data.get("url")
        if image_url:
            if isinstance(image_url, str):
                image_url = {"url": image_url}
            return {"type": "image_url", "image_url": image_url}
        payload = data.get("data") or data.get("base64") or data.get("content")
        media_type = data.get("media_type") or data.get("mime_type")
        if payload and media_type:
            if isinstance(payload, bytes):
                payload = base64.b64encode(payload).decode("ascii")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{payload}"},
            }
        return None

    @staticmethod
    def _audio_part(audio: Any) -> dict[str, Any] | None:
        data = _model_dump(audio) if not isinstance(audio, dict) else dict(audio)
        payload = data.get("data") or data.get("content")
        audio_format = data.get("format")
        if payload and audio_format:
            if isinstance(payload, bytes):
                payload = base64.b64encode(payload).decode("ascii")
            return {
                "type": "input_audio",
                "input_audio": {"data": payload, "format": audio_format},
            }
        return None

    def _content_from_message(self, message: Any) -> Any:
        content = _first_attr(message, "content", default=None)
        parts: list[dict[str, Any]] = []
        if isinstance(content, str):
            if content:
                parts.append({"type": "text", "text": content})
        elif content is not None and not isinstance(content, list):
            parts.append({"type": "text", "text": str(content)})
        elif isinstance(content, list):
            for block in content:
                data = _model_dump(block) if not isinstance(block, dict) else dict(block)
                block_type = data.get("type")
                if block_type == "text":
                    parts.append({"type": "text", "text": data.get("text", "")})
                elif block_type == "image":
                    part = self._image_part(data)
                    if part:
                        parts.append(part)
                elif block_type in {"image_url", "input_image", "audio", "input_audio"}:
                    parts.append(data)

        images = _first_attr(message, "images", default=None) or []
        if not isinstance(images, list):
            images = [images]
        for image in images:
            part = self._image_part(image)
            if part:
                parts.append(part)
        audio_inputs = _first_attr(message, "audio", default=None) or []
        if not isinstance(audio_inputs, list):
            audio_inputs = [audio_inputs]
        for audio in audio_inputs:
            part = self._audio_part(audio)
            if part:
                parts.append(part)
        if len(parts) == 1 and parts[0].get("type") == "text":
            return parts[0]["text"]
        return parts or None

    def _format_message(self, message: Any) -> list[dict[str, Any]]:
        if message is None:
            return []
        if isinstance(message, ModelResponse):
            data = {
                "role": message.role or "assistant",
                "content": message.content,
            }
            message = data
        elif isinstance(message, str):
            return [{"role": "user", "content": message}]

        data = _model_dump(message)
        role = self._map_role(str(data.get("role") or "user"))
        content = self._content_from_message(message)

        formatted: list[dict[str, Any]] = []
        if role in {"system", "developer", "user", "assistant"} and content not in (
            None,
            "",
            [],
        ):
            entry = {"role": role, "content": content}
            if data.get("name"):
                entry["name"] = data["name"]
            formatted.append(entry)

        return formatted

    def _format_all_messages(self, messages: Any) -> list[dict[str, Any]]:
        if isinstance(messages, (str, dict, BaseModel)):
            messages = [messages]
        formatted: list[dict[str, Any]] = []
        for message in messages or []:
            formatted.extend(self._format_message(message))
        return formatted

    def _apply_usage(self, response: ModelResponse, usage: CompletionUsage) -> None:
        response.input_tokens = usage.prompt_tokens or 0
        response.output_tokens = usage.completion_tokens or 0
        response.total_tokens = usage.total_tokens or 0

        prompt_details = getattr(usage, "prompt_tokens_details", None)
        if prompt_details is not None:
            response.cache_read_tokens = getattr(prompt_details, "cached_tokens", None)
        completion_details = getattr(usage, "completion_tokens_details", None)
        if completion_details is not None:
            response.reasoning_tokens = getattr(completion_details, "reasoning_tokens", None)

        response.extra = {
            **(response.extra or {}),
            "usage": {
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "total_tokens": response.total_tokens,
                "reasoning_tokens": response.reasoning_tokens,
                "cache_read_tokens": response.cache_read_tokens,
            },
        }

    def _parse_structured_content(
        self,
        content: Any,
        response_format: dict[str, Any] | type[BaseModel] | None,
    ) -> Any:
        if response_format is None or not isinstance(content, str):
            return None
        if isinstance(response_format, type) and issubclass(response_format, BaseModel):
            try:
                return response_format.model_validate_json(content)
            except ValueError as exc:
                raise ModelProviderError(
                    "OpenAI response does not match the requested response_format",
                    model_name=self.name,
                    model_id=self.id,
                ) from exc
        if isinstance(response_format, dict) and response_format.get("type") in {
            "json_object",
            "json_schema",
        }:
            try:
                return json.loads(content)
            except json.JSONDecodeError as exc:
                raise ModelProviderError(
                    "OpenAI response is not valid JSON",
                    model_name=self.name,
                    model_id=self.id,
                ) from exc
        return None

    def _parse_provider_response(
        self,
        response: ChatCompletion,
        response_format: dict[str, Any] | type[BaseModel] | None = None,
    ) -> ModelResponse:
        if not getattr(response, "choices", None):
            raise ModelProviderError(
                "OpenAI returned no completion choices",
                model_name=self.name,
                model_id=self.id,
            )

        choice = response.choices[0]
        provider_message = choice.message
        content = getattr(provider_message, "content", None)
        parsed = self._parse_structured_content(content, response_format)
        reasoning = getattr(provider_message, "reasoning_content", None) or getattr(
            provider_message, "reasoning", None
        )

        model_response = ModelResponse(
            index=0,
            block_type="message",
            role=getattr(provider_message, "role", None) or "assistant",
            content=content,
            parsed=parsed,
            reasoning_content=reasoning,
            provider_data={
                "id": getattr(response, "id", None),
                "model": getattr(response, "model", None),
                "created": getattr(response, "created", None),
                "system_fingerprint": getattr(response, "system_fingerprint", None),
            },
            extra={"finish_reason": getattr(choice, "finish_reason", None)},
        )
        if response.usage is not None:
            self._apply_usage(model_response, response.usage)

        audio = getattr(provider_message, "audio", None)
        if audio is not None:
            model_response.extra = {
                **(model_response.extra or {}),
                "audio": _model_dump(audio),
            }
            transcript = getattr(audio, "transcript", None)
            if transcript and not model_response.content:
                model_response.content = transcript
        refusal = getattr(provider_message, "refusal", None)
        if refusal:
            model_response.extra = {
                **(model_response.extra or {}),
                "refusal": refusal,
            }
        return model_response

    def _parse_provider_response_delta(self, chunk: ChatCompletionChunk) -> ModelResponse:
        choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
        delta: ChoiceDelta | None = getattr(choice, "delta", None) if choice else None
        finish_reason = getattr(choice, "finish_reason", None) if choice else None
        usage = getattr(chunk, "usage", None)

        if delta is not None and delta.content is not None:
            block_type = "content_delta"
            content: Any = delta.content
        elif delta is not None and (
            getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        ):
            block_type = "reasoning_delta"
            content = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        elif finish_reason is not None:
            block_type = "finish"
            content = None
        elif usage is not None:
            block_type = "usage"
            content = None
        else:
            block_type = "delta"
            content = None

        response = ModelResponse(
            index=int(getattr(choice, "index", 0) or 0),
            block_type=block_type,
            role="assistant",
            content=content,
            reasoning_content=content if block_type == "reasoning_delta" else None,
            provider_data={
                "id": getattr(chunk, "id", None),
                "model": getattr(chunk, "model", None),
                "created": getattr(chunk, "created", None),
                "system_fingerprint": getattr(chunk, "system_fingerprint", None),
            },
            extra={"finish_reason": finish_reason} if finish_reason else None,
        )
        if usage is not None:
            self._apply_usage(response, usage)
        return response

    def _map_error(self, error: Exception) -> Exception:
        if isinstance(error, (ModelAuthenticationError, ModelProviderError)):
            return error

        if isinstance(error, AuthenticationError):
            return ModelAuthenticationError(
                str(error),
                status_code=getattr(error, "status_code", 401),
                model_name=self.name,
                model_id=self.id,
            )
        if isinstance(error, RateLimitError):
            return ModelProviderError(
                str(error),
                status_code=getattr(error, "status_code", 429),
                model_name=self.name,
                model_id=self.id,
            )
        if isinstance(error, APIConnectionError):
            return ModelProviderError(
                str(error),
                model_name=self.name,
                model_id=self.id,
            )
        if isinstance(error, APIStatusError):
            message = str(error)
            code = None
            try:
                body = error.response.json()
                provider_error = body.get("error", body) if isinstance(body, dict) else body
                if isinstance(provider_error, dict):
                    message = provider_error.get("message", message)
                    code = provider_error.get("code")
            except (AttributeError, TypeError, ValueError) as parse_error:
                logger.debug("Could not parse provider error response: %s", parse_error)
            if code == "context_length_exceeded" or "context length" in message.lower():
                return ContextWindowExceededError(
                    message,
                    status_code=getattr(error, "status_code", None),
                    model_name=self.name,
                    model_id=self.id,
                )
            return ModelProviderError(
                message,
                status_code=getattr(error, "status_code", None),
                model_name=self.name,
                model_id=self.id,
            )
        return ModelProviderError(
            str(error),
            model_name=self.name,
            model_id=self.id,
        )

    def _start_timer(self, assistant_message: Any) -> float:
        metrics = getattr(assistant_message, "metrics", None)
        starter = getattr(metrics, "start_timer", None)
        if callable(starter):
            starter()
        return monotonic()

    def _finish_timer(self, assistant_message: Any, started_at: float) -> float:
        metrics = getattr(assistant_message, "metrics", None)
        stopper = getattr(metrics, "stop_timer", None)
        if callable(stopper):
            stopper()
        return monotonic() - started_at

    @staticmethod
    def _populate_assistant_message(assistant_message: Any, response: ModelResponse) -> None:
        if assistant_message is None:
            return
        for name in (
            "role",
            "content",
            "parsed",
            "reasoning_content",
            "provider_data",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "reasoning_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "time_to_first_token",
        ):
            if hasattr(assistant_message, name):
                setattr(assistant_message, name, getattr(response, name))

    def _raise_mapped_error(self, error: Exception) -> NoReturn:
        mapped = self._map_error(error)
        if mapped is error:
            raise error
        raise mapped from error

    def _create_sync(self, params: dict[str, Any], stream: bool) -> Any:
        client = self.get_client()
        request = {**params, "stream": True} if stream else dict(params)
        if stream:
            request["stream_options"] = {"include_usage": True}
        try:
            return client.chat.completions.create(**request)
        except BadRequestError as exc:
            if not stream or "stream_options" not in str(exc).lower():
                raise
            request.pop("stream_options", None)
            return client.chat.completions.create(**request)

    async def _create_async(self, params: dict[str, Any], stream: bool) -> Any:
        client = self.get_async_client()
        request = {**params, "stream": True} if stream else dict(params)
        if stream:
            request["stream_options"] = {"include_usage": True}
        try:
            return await client.chat.completions.create(**request)
        except BadRequestError as exc:
            if not stream or "stream_options" not in str(exc).lower():
                raise
            request.pop("stream_options", None)
            return await client.chat.completions.create(**request)

    def invoke(
        self,
        messages: Any,
        assistant_message: Any = None,
        response_format: dict[str, Any] | type[BaseModel] | None = None,
        run_response: Any = None,
        **kwargs: Any,
    ) -> ModelResponse:
        started_at = self._start_timer(assistant_message)
        try:
            params = {
                "model": self.id,
                "messages": self._format_all_messages(messages),
                **self.get_request_params(
                    response_format=response_format,
                    run_response=run_response,
                ),
                **kwargs,
            }
            provider_response = self._create_sync(params, stream=False)
            response = self._parse_provider_response(provider_response, response_format)
            self._finish_timer(assistant_message, started_at)
            self._populate_assistant_message(assistant_message, response)
            return response
        except Exception as exc:  # noqa: BLE001 - normalize every provider failure
            logger.error("OpenAI chat request failed: %s", exc)
            self._raise_mapped_error(exc)

    async def ainvoke(
        self,
        messages: Any,
        assistant_message: Any = None,
        response_format: dict[str, Any] | type[BaseModel] | None = None,
        run_response: Any = None,
        **kwargs: Any,
    ) -> ModelResponse:
        started_at = self._start_timer(assistant_message)
        try:
            params = {
                "model": self.id,
                "messages": self._format_all_messages(messages),
                **self.get_request_params(
                    response_format=response_format,
                    run_response=run_response,
                ),
                **kwargs,
            }
            provider_response = await self._create_async(params, stream=False)
            response = self._parse_provider_response(provider_response, response_format)
            self._finish_timer(assistant_message, started_at)
            self._populate_assistant_message(assistant_message, response)
            return response
        except Exception as exc:  # noqa: BLE001 - normalize every provider failure
            logger.error("OpenAI async chat request failed: %s", exc)
            self._raise_mapped_error(exc)

    def invoke_stream(
        self,
        messages: Any,
        assistant_message: Any = None,
        response_format: dict[str, Any] | type[BaseModel] | None = None,
        run_response: Any = None,
        **kwargs: Any,
    ) -> Iterator[ModelResponse]:
        started_at = self._start_timer(assistant_message)
        try:
            params = {
                "model": self.id,
                "messages": self._format_all_messages(messages),
                **self.get_request_params(
                    response_format=response_format,
                    run_response=run_response,
                ),
                **kwargs,
            }
            stream = self._create_sync(params, stream=True)
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            finish_reason: str | None = None
            first_output_at: float | None = None
            last_usage: ModelResponse | None = None

            for chunk in stream:
                parsed = self._parse_provider_response_delta(chunk)
                if first_output_at is None and (
                    parsed.content is not None or parsed.reasoning_content is not None
                ):
                    first_output_at = monotonic()
                if parsed.input_tokens is not None:
                    last_usage = parsed
                if parsed.block_type == "content_delta" and parsed.content is not None:
                    text_parts.append(str(parsed.content))
                if parsed.block_type == "reasoning_delta" and parsed.reasoning_content:
                    reasoning_parts.append(parsed.reasoning_content)
                if parsed.extra and parsed.extra.get("finish_reason"):
                    finish_reason = parsed.extra["finish_reason"]
                if (
                    parsed.block_type != "delta"
                    or parsed.content is not None
                    or parsed.input_tokens is not None
                    or finish_reason
                ):
                    yield parsed

            final = ModelResponse(
                index=0,
                block_type="message",
                role="assistant",
                content="".join(text_parts) or None,
                parsed=self._parse_structured_content("".join(text_parts), response_format),
                reasoning_content="".join(reasoning_parts) or None,
                extra={"finish_reason": finish_reason} if finish_reason else None,
                time_to_first_token=(
                    first_output_at - started_at if first_output_at is not None else None
                ),
            )
            if last_usage is not None:
                final.input_tokens = last_usage.input_tokens
                final.output_tokens = last_usage.output_tokens
                final.total_tokens = last_usage.total_tokens
                final.reasoning_tokens = last_usage.reasoning_tokens
                final.cache_read_tokens = last_usage.cache_read_tokens
                final.cache_write_tokens = last_usage.cache_write_tokens
                final.extra = {**(final.extra or {}), **(last_usage.extra or {})}
            self._finish_timer(assistant_message, started_at)
            self._populate_assistant_message(assistant_message, final)
        except Exception as exc:  # noqa: BLE001 - normalize every provider failure
            logger.error("OpenAI streaming request failed: %s", exc)
            self._raise_mapped_error(exc)

    async def ainvoke_stream(
        self,
        messages: Any,
        assistant_message: Any = None,
        response_format: dict[str, Any] | type[BaseModel] | None = None,
        run_response: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelResponse]:
        started_at = self._start_timer(assistant_message)
        try:
            params = {
                "model": self.id,
                "messages": self._format_all_messages(messages),
                **self.get_request_params(
                    response_format=response_format,
                    run_response=run_response,
                ),
                **kwargs,
            }
            stream = await self._create_async(params, stream=True)
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            finish_reason: str | None = None
            first_output_at: float | None = None
            last_usage: ModelResponse | None = None

            async for chunk in stream:
                parsed = self._parse_provider_response_delta(chunk)
                if first_output_at is None and (
                    parsed.content is not None or parsed.reasoning_content is not None
                ):
                    first_output_at = monotonic()
                if parsed.input_tokens is not None:
                    last_usage = parsed
                if parsed.block_type == "content_delta" and parsed.content is not None:
                    text_parts.append(str(parsed.content))
                if parsed.block_type == "reasoning_delta" and parsed.reasoning_content:
                    reasoning_parts.append(parsed.reasoning_content)
                if parsed.extra and parsed.extra.get("finish_reason"):
                    finish_reason = parsed.extra["finish_reason"]
                if (
                    parsed.block_type != "delta"
                    or parsed.content is not None
                    or parsed.input_tokens is not None
                    or finish_reason
                ):
                    yield parsed

            final = ModelResponse(
                index=0,
                block_type="message",
                role="assistant",
                content="".join(text_parts) or None,
                parsed=self._parse_structured_content("".join(text_parts), response_format),
                reasoning_content="".join(reasoning_parts) or None,
                extra={"finish_reason": finish_reason} if finish_reason else None,
                time_to_first_token=(
                    first_output_at - started_at if first_output_at is not None else None
                ),
            )
            if last_usage is not None:
                final.input_tokens = last_usage.input_tokens
                final.output_tokens = last_usage.output_tokens
                final.total_tokens = last_usage.total_tokens
                final.reasoning_tokens = last_usage.reasoning_tokens
                final.cache_read_tokens = last_usage.cache_read_tokens
                final.cache_write_tokens = last_usage.cache_write_tokens
                final.extra = {**(final.extra or {}), **(last_usage.extra or {})}
            self._finish_timer(assistant_message, started_at)
            self._populate_assistant_message(assistant_message, final)
        except Exception as exc:  # noqa: BLE001 - normalize every provider failure
            logger.error("OpenAI async streaming request failed: %s", exc)
            self._raise_mapped_error(exc)


__all__ = [
    "ContextWindowExceededError",
    "ModelAuthenticationError",
    "ModelProviderError",
    "OpenAIChat",
]
