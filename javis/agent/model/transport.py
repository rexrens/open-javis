"""HTTP transport and incremental decoding for Server-Sent Events."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from typing import Any
import re

import httpx


@dataclass(slots=True)
class ServerSentEvent:
    """One complete SSE event."""

    event: str | None = None
    data: str | None = None
    id: str | None = None
    retry: int | None = None

_SSE_LINE_END = re.compile(b"[\\r\\n]")

class _SSELineDecoder:
    """Incrementally split CR, LF, and CRLF without copying growing prefixes."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._skip_lf = False

    def _append(self, chunk: bytes, start: int, end: int) -> None:
        self._buffer.extend(memoryview(chunk)[start:end])

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        start = 0
        for match in _SSE_LINE_END.finditer(chunk):
            end = match.start()
            if self._skip_lf and end == start and chunk[end] == 10:
                self._skip_lf = False
                start = end + 1
                continue
            self._skip_lf = False
            self._append(chunk, start, end)
            line = bytes(self._buffer)
            self._buffer.clear()
            self._skip_lf = chunk[end] == 13
            start = end + 1
            yield line
        if start < len(chunk):
            self._skip_lf = False
            self._append(chunk, start, len(chunk))

    def finish(self) -> Iterator[bytes]:
        if self._buffer:
            line = bytes(self._buffer)
            self._buffer.clear()
            yield line


class SSEDecoder:
    """Decode SSE incrementally without imposing a line or event size limit."""

    _data: list[str]
    _event: str | None
    _retry: int | None
    _last_event_id: str | None

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._event = None
        self._data = []
        self._last_event_id = None
        self._retry = None

    def _decode_lines(self, lines: Iterator[bytes]) -> Iterator[ServerSentEvent]:
        for raw_line in lines:
            sse = self.decode(raw_line.decode("utf-8"))
            if sse:
                yield sse

    def iter_bytes(self, iterator: Iterator[bytes]) -> Iterator[ServerSentEvent]:
        """Given raw binary data, yield every event encountered."""
        decoder = _SSELineDecoder()
        try:
            for chunk in iterator:
                yield from self._decode_lines(decoder.feed(chunk))
            yield from self._decode_lines(decoder.finish())
        finally:
            self._reset()

    async def aiter_bytes(self, iterator: AsyncIterator[bytes]) -> AsyncIterator[ServerSentEvent]:
        """Given async raw binary data, yield every event encountered."""
        decoder = _SSELineDecoder()
        try:
            async for chunk in iterator:
                for sse in self._decode_lines(decoder.feed(chunk)):
                    yield sse
            for sse in self._decode_lines(decoder.finish()):
                yield sse
        finally:
            self._reset()

    def decode(self, line: str) -> ServerSentEvent | None:
        # See: https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation  # noqa: E501

        if not line:
            if not self._event and not self._data and not self._last_event_id and self._retry is None:
                return None

            sse = ServerSentEvent(
                event=self._event,
                data="\n".join(self._data),
                id=self._last_event_id,
                retry=self._retry,
            )

            # NOTE: as per the SSE spec, do not reset last_event_id.
            self._event = None
            self._data = []
            self._retry = None

            return sse

        if line.startswith(":"):
            return None

        fieldname, _, value = line.partition(":")

        if value.startswith(" "):
            value = value[1:]

        if fieldname == "event":
            self._event = value
        elif fieldname == "data":
            self._data.append(value)
        elif fieldname == "id":
            if "\0" in value:
                pass
            else:
                self._last_event_id = value
        elif fieldname == "retry":
            try:
                self._retry = int(value)
            except (TypeError, ValueError):
                pass
        else:
            pass  # Field is ignored.

        return None

class ServerSentEventTransport:
    """POST a JSON body and consume the response as Server-Sent Events."""

    def __init__(
        self,
        timeout: float = 300.0,
        verify_ssl: bool = True,
        proxy: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.proxy = proxy
        self._decoder = SSEDecoder()

    def stream(
        self,
        url: str,
        headers: Mapping[str, str],
        body: dict[str, Any],
    ) -> Iterator[ServerSentEvent]:
        with (
            httpx.Client(
                timeout=self.timeout,
                verify=self.verify_ssl,
                proxy=self.proxy,
            ) as client,
            client.stream("POST", url, headers=headers, json=body) as response,
        ):
            response.raise_for_status()
            for sse in self._decoder.iter_bytes(response.iter_bytes()):
                yield sse

    async def astream(
        self,
        url: str,
        headers: Mapping[str, str],
        body: dict[str, Any],
    ) -> AsyncIterator[ServerSentEvent]:
        async with (
            httpx.AsyncClient(
                timeout=self.timeout,
                verify=self.verify_ssl,
                proxy=self.proxy,
            ) as client,
            client.stream("POST", url, headers=headers, json=body) as response,
        ):
            response.raise_for_status()
            async for sse in self._decoder.aiter_bytes(response.aiter_bytes()):
                yield sse
