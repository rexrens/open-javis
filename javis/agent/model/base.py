from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from time import time
from typing import Any, ClassVar


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
class ModelResponse:
    """Response from the model provider."""

    index: int
    block_type: str
    role: str | None = None
    content: Any | None = None
    parsed: Any | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    provider_data: dict[str, Any] | None = None
    redacted_reasoning_content: str | None = None
    reasoning_content: str | None = None
    created_at: int = field(default_factory=lambda: int(time()))
    extra: dict[str, Any] | None = None
    updated_session_state: dict[str, Any] | None = None
    compression_stats: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    time_to_first_token: float | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass
class Model(ABC):
    # Provider for this Model, AS[OPEN]. This is not sent to the Model API.
    provider: ClassVar[str] = "unknown"

    id: str = ""
    name: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    timeout: int | None = None
    max_retries: int = 3
    headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Model ID cannot be empty")
        self.name = self.name or self.id
        self.extra_body = self.extra_body or {}

    @abstractmethod
    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        pass

    @abstractmethod
    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        pass

    @abstractmethod
    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[ModelResponse]:
        pass

    @abstractmethod
    def ainvoke_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ModelResponse]:
        pass
