"""LLMAdapter — the provider-wire adapter contract (dsh ``LlmAdapter``).

Port of ``packages/llm/llm/src/index.ts``'s abstract adapter: a provider
backend implements **``stream(GenerateOptions)`` → ``StreamChunk``** — the
only abstract method — consuming the core message vocabulary directly (no
intermediate provider model; serialization lives inside the adapter).

Optional overrides (dsh defaults kept):
- ``provider_info(provider)`` — display metadata for a route it owns
- ``provider_retry_policy(provider)`` — provider-owned retry policy captured
  at registration (the loop's ``agent/request-error`` listens to it)
- ``list_models(provider)`` — advisory catalog for discovery consumers
- ``resolve_model(provider, model, signal)`` — exact-model metadata
  (context window / default max tokens) for ``prepare_call``
- ``prepare_call(provider, model, signal)`` — bind one generation's metadata
  and dispatch entry point (dynamic adapters override this)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from javis.harness.types import AbortSignal, GenerateOptions, StreamChunk


@dataclass(frozen=True)
class LlmProviderInfo:
    """Detached display metadata for one provider route (dsh ``LlmProviderInfo``)."""

    id: str
    name: str


@dataclass(frozen=True)
class LlmResolvedModelInfo:
    """Exact-model metadata resolved by the adapter (dsh ``LlmResolvedModelInfo``)."""

    provider: str
    id: str
    name: str
    #: Context capacity the loop logs in the request header (``contextWindow``).
    context_window: int | None = None
    #: Provider-owned default output cap (``defaultMaxTokens``), materialized
    #: into the config when the caller left ``max_tokens`` unset.
    default_max_tokens: int | None = None


@dataclass(frozen=True)
class LlmModelInfo:
    """Advisory catalog entry (dsh ``LlmModelInfo``); never validates routing."""

    provider: str
    id: str
    name: str
    description: str | None = None


@dataclass(frozen=True)
class PreparedAdapterCall:
    """One generation's model metadata and dispatch entry point (dsh ``PreparedAdapterCall``)."""

    model: LlmResolvedModelInfo
    stream: Callable[[GenerateOptions], AsyncIterator[StreamChunk]] = field(
        repr=False, compare=False
    )


class LLMAdapter(ABC):
    """Provider-wire adapter for the harness message and stream vocabulary.

    Register implementations with ``LlmRuntime.register_adapter``.
    """

    def provider_info(self, provider: str) -> LlmProviderInfo:
        """Describe one provider route owned by this adapter."""
        return LlmProviderInfo(id=provider, name=provider)

    def provider_retry_policy(self, provider: str) -> dict[str, Any] | None:
        """Provider-owned retry policy captured with this route (None = defaults)."""
        del provider
        return None

    async def list_models(self, provider: str) -> list[LlmModelInfo]:
        """Advisory models this adapter advertises for one owned provider."""
        del provider
        return []

    async def resolve_model(
        self,
        provider: str,
        model: str,
        signal: AbortSignal | None = None,
    ) -> LlmResolvedModelInfo:
        """Resolve exact-model metadata (independent of the advisory catalog)."""
        del signal
        return LlmResolvedModelInfo(provider=provider, id=model, name=model)

    async def prepare_call(
        self,
        provider: str,
        model: str,
        signal: AbortSignal | None = None,
    ) -> PreparedAdapterCall:
        """Bind exact-model metadata and dispatch to one adapter generation."""
        resolved = await self.resolve_model(provider, model, signal)
        return PreparedAdapterCall(
            model=resolved,
            stream=lambda options: self.stream(options),
        )

    @abstractmethod
    def stream(self, options: GenerateOptions) -> AsyncIterator[StreamChunk]:
        """Stream one model call as raw chunks. The only required method.

        Must honor ``options.signal`` (abort → raise ``AbortError`` or stop).
        Failures may throw; ``LlmRuntime`` normalizes them into a terminal
        ``error``/``aborted`` finish chunk.
        """


__all__ = [
    "LLMAdapter",
    "LlmModelInfo",
    "LlmProviderInfo",
    "LlmResolvedModelInfo",
    "PreparedAdapterCall",
]
