from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

from javis.agent.model.request import ModelRequest
from javis.agent.model.response import ModelResponse
from javis.agent.model.transport import ServerSentEventTransport
from javis.harness.types import StreamChunk


class ModelError(Exception):
    """Base exception for model adapter failures."""


class ModelProviderError(ModelError):
    """A normalized error returned by a model provider."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        model_name: str | None = None,
        model_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.model_name = model_name
        self.model_id = model_id

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"[{self.status_code}] {self.message}"


class ModelAuthenticationError(ModelProviderError):
    """The provider rejected the configured credentials."""


class ContextWindowExceededError(ModelProviderError):
    """The request is too large for the model context window."""


@dataclass
class Model(ABC):
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 3,
        max_retries: int = 3,
        headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        # Provider for this Model, AS[OPEN]. This is not sent to the Model API.
        self.model = model
        self.api_key = api_key
        self.base_url = base_url or ""
        self.timeout = timeout
        self.max_retries = max_retries
        self.headers = headers or {}
        self.extra_body = extra_body or {}
        self._transport = ServerSentEventTransport(
            timeout=timeout,
        )

    @abstractmethod
    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        raise NotImplementedError

    @abstractmethod
    async def ainvoke(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    @abstractmethod
    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[StreamChunk]:
        raise NotImplementedError

    @abstractmethod
    def ainvoke_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError
