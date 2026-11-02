"""The provider-neutral model-call service (``ctx.llm``).

Adapters register one or more provider routes and stream raw chunks; the
service is the only supported path into a provider and guarantees that every
stream ends in exactly one terminal ``finish`` chunk (``error`` when an
adapter raised, ``aborted`` when the caller cancelled).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from .types import GenerateOptions, LlmFailure, finish_chunk

if TYPE_CHECKING:
    from javis.cordis import Context


class LlmError(Exception):
    """A provider or transport failure with a stable machine-routing ``code``."""

    def __init__(self, code: str, message: str | None = None, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(message if message is not None else code)

    def failure(self) -> LlmFailure:
        return LlmFailure(message=str(self), code=self.code, status=self.status)


class LlmAdapter:
    """Provider-wire adapter for the harness message and stream vocabulary."""

    def list_models(self, provider: str) -> list[str]:
        """Advisory model ids this route advertises (empty when unknown)."""
        return []

    async def stream(self, options: GenerateOptions) -> AsyncIterator[dict[str, Any]]:
        """Stream one model call, ending with a ``finish`` chunk."""
        raise NotImplementedError
        yield  # pragma: no cover - marks this function as an async generator


class LlmService:
    """Model-call service installed as ``ctx.llm``."""

    def __init__(self, ctx: "Context"):
        self.ctx = ctx
        self._adapters: dict[str, LlmAdapter] = {}

    # -- registry -----------------------------------------------------------

    def register_adapter(self, providers: list[str], adapter: LlmAdapter) -> Callable[[], None]:
        """Register ``adapter`` for every route in ``providers``.

        Returns the disposer that releases every route this call owns.
        Registering a route that is already held fails with
        ``DUPLICATE_ADAPTER`` and leaves the registry untouched.
        """
        providers = [providers] if isinstance(providers, str) else list(providers)
        for provider in providers:
            if provider in self._adapters:
                raise LlmError("DUPLICATE_ADAPTER", f'adapter for provider "{provider}" is already registered')
        for provider in providers:
            self._adapters[provider] = adapter
        self.ctx.emit("llm/adapters-updated")

        disposed = False

        def disposer() -> None:
            nonlocal disposed
            if disposed:
                return
            disposed = True
            for provider in providers:
                if self._adapters.get(provider) is adapter:
                    del self._adapters[provider]
            self.ctx.emit("llm/adapters-updated")

        return disposer

    def list_providers(self) -> list[str]:
        return list(self._adapters)

    def get_adapter(self, provider: str) -> LlmAdapter | None:
        return self._adapters.get(provider)

    def list_models(self, provider: str) -> list[str]:
        adapter = self._adapters.get(provider)
        return adapter.list_models(provider) if adapter is not None else []

    # -- dispatch -----------------------------------------------------------

    async def stream(self, options: GenerateOptions) -> AsyncIterator[dict[str, Any]]:
        """Stream one request, normalizing failures into a terminal ``finish``."""
        adapter = self._adapters.get(options.provider)
        if adapter is None:
            yield finish_chunk(
                "error",
                LlmFailure(
                    message=f'no adapter registered for provider "{options.provider}"',
                    code="NO_ADAPTER",
                ),
            )
            return

        terminal = False
        try:
            async for chunk in adapter.stream(options):
                if chunk.get("type") == "finish":
                    terminal = True
                yield chunk
        except asyncio.CancelledError:
            if not terminal:
                yield finish_chunk(
                    "aborted",
                    LlmFailure(message="caller cancelled the request", code="ABORTED"),
                )
            raise
        except LlmError as error:
            yield finish_chunk("error", error.failure())
            return
        except Exception as error:  # noqa: BLE001 - adapters may raise anything
            yield finish_chunk(
                "error",
                LlmFailure(message=f"{type(error).__name__}: {error}", code="ERROR"),
            )
            return

        if not terminal:
            # An adapter that ended without a terminal chunk still owes the
            # consumer one: report a clean stop rather than a dangling stream.
            yield finish_chunk("stop")
