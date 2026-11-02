"""Endpoint registry: ``"<namespace>/<method>"`` to handler dispatch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, overload

from javis.app.web.protocol import RemoteError


class StreamSink(Protocol):
    """Sink one stream handler pushes frames into."""

    async def send(self, value: Any) -> None:
        """Send one item value on the open logical stream."""
        ...


UnaryHandler = Callable[[dict[str, Any]], Awaitable[Any]]
StreamHandler = Callable[[dict[str, Any], StreamSink], Awaitable[None]]


class EndpointRegistry:
    """The set of Remote endpoints this host implements.

    Unknown endpoints answer a structured ``gateway/unknown-endpoint`` failure
    instead of a 500, so a partially implemented surface degrades per panel.
    """

    def __init__(self) -> None:
        self._unary: dict[str, UnaryHandler] = {}
        self._stream: dict[str, StreamHandler] = {}

    @overload
    def unary(self, endpoint: str) -> Callable[[UnaryHandler], UnaryHandler]: ...

    @overload
    def unary(self, endpoint: str, handler: UnaryHandler) -> None: ...

    def unary(
        self, endpoint: str, handler: UnaryHandler | None = None
    ) -> Callable[[UnaryHandler], UnaryHandler] | None:
        """Register one unary endpoint, directly or as a decorator."""
        if handler is None:

            def decorator(function: UnaryHandler) -> UnaryHandler:
                self._unary[endpoint] = function
                return function

            return decorator
        self._unary[endpoint] = handler
        return None

    @overload
    def stream(self, endpoint: str) -> Callable[[StreamHandler], StreamHandler]: ...

    @overload
    def stream(self, endpoint: str, handler: StreamHandler) -> None: ...

    def stream(
        self, endpoint: str, handler: StreamHandler | None = None
    ) -> Callable[[StreamHandler], StreamHandler] | None:
        """Register one streaming endpoint, directly or as a decorator."""
        if handler is None:

            def decorator(function: StreamHandler) -> StreamHandler:
                self._stream[endpoint] = function
                return function

            return decorator
        self._stream[endpoint] = handler
        return None

    def unary_handler(self, endpoint: str) -> UnaryHandler:
        """Look up a unary handler, or raise the unknown-endpoint failure."""
        handler = self._unary.get(endpoint)
        if handler is None:
            raise RemoteError(
                "gateway/unknown-endpoint",
                f"endpoint {endpoint!r} is not implemented by this javis host",
                {"endpoint": endpoint},
            )
        return handler

    def stream_handler(self, endpoint: str) -> StreamHandler:
        """Look up a stream handler, or raise the unknown-endpoint failure."""
        handler = self._stream.get(endpoint)
        if handler is None:
            raise RemoteError(
                "gateway/unknown-endpoint",
                f"stream {endpoint!r} is not implemented by this javis host",
                {"endpoint": endpoint},
            )
        return handler

    def implemented(self) -> list[str]:
        """Every implemented endpoint, sorted — used by tests and ``doctor``."""
        return sorted([*self._unary, *self._stream])
