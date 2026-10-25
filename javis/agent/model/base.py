from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from time import time
from typing import (
    Any,
)


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
    """Response from the model provider"""

    index: int

    block_type: str

    role: str | None = None

    content: Any | None = None

    parsed: Any | None = None

    # Unified media fields for LLM-generated and tool-generated media artifacts
    # images: Optional[List[Image]] = None
    # videos: Optional[List[Video]] = None
    # audios: Optional[List[Audio]] = None
    # files: Optional[List[File]] = None

    # Model tool calls
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    provider_data: dict[str, Any] | None = None

    redacted_reasoning_content: str | None = None

    reasoning_content: str | None = None

    # citations: Optional[Citations] = None

    # response_usage: Optional[MessageMetrics] = None

    created_at: int = field(default_factory=lambda: int(time()))

    extra: dict[str, Any] | None = None

    updated_session_state: dict[str, Any] | None = None

    # Compression stats
    compression_stats: dict[str, Any] | None = None

    # Model request metrics (for model_request_completed events)
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    time_to_first_token: float | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass
class Model(ABC):
    id: str
    name: str | None = None
    # Provider for this Model. This is not sent to the Model API.
    provider: str | None = None

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
