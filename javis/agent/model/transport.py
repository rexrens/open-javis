"""HTTP transport and incremental decoding for Server-Sent Events."""

from __future__ import annotations

import codecs
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class ServerSentEvent:
    """One complete SSE event."""

    event: str | None = None
    data: str | None = None
    id: str | None = None
    retry: int | None = None


class _LineBuffer:
    """Incrementally decode UTF-8 bytes into SSE lines."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._at_start = True

    def feed(self, chunk: bytes) -> list[str]:
        text = self._decoder.decode(chunk)
        if self._at_start and text:
            text = text.removeprefix("\ufeff")
            self._at_start = False
        self._buffer += text
        return self._take_lines(final=False)

    def finish(self) -> list[str]:
        self._buffer += self._decoder.decode(b"", final=True)
        return self._take_lines(final=True)

    def _take_lines(self, *, final: bool) -> list[str]:
        lines: list[str] = []

        while self._buffer:
            cr = self._buffer.find("\r")
            lf = self._buffer.find("\n")
            if cr == -1:
                line_end = lf
            elif lf == -1:
                line_end = cr
            else:
                line_end = min(cr, lf)

            if line_end == -1:
                break

            if not final and self._buffer[line_end] == "\r" and line_end == len(self._buffer) - 1:
                break

            consume = 1
            if self._buffer[line_end] == "\r" and self._buffer[line_end + 1 :].startswith("\n"):
                consume = 2

            lines.append(self._buffer[:line_end])
            self._buffer = self._buffer[line_end + consume :]

        if final and self._buffer:
            lines.append(self._buffer)
            self._buffer = ""

        return lines


class ServerSentEventDecoder:
    """Decode arbitrary byte chunks into complete SSE events."""

    def __init__(self) -> None:
        self._event: str | None = None
        self._data: list[str] = []
        self._last_event_id: str | None = None
        self._retry: int | None = None

    def iter_bytes(self, iterator: Iterator[bytes]) -> Iterator[ServerSentEvent]:
        lines = _LineBuffer()

        for chunk in iterator:
            for line in lines.feed(chunk):
                event = self._decode(line)
                if event is not None:
                    yield event

        for line in lines.finish():
            event = self._decode(line)
            if event is not None:
                yield event

    async def aiter_bytes(self, iterator: AsyncIterator[bytes]) -> AsyncIterator[ServerSentEvent]:
        lines = _LineBuffer()

        async for chunk in iterator:
            for line in lines.feed(chunk):
                event = self._decode(line)
                if event is not None:
                    yield event

        for line in lines.finish():
            event = self._decode(line)
            if event is not None:
                yield event

    def _decode(self, line: str) -> ServerSentEvent | None:
        if not line:
            return self._dispatch()

        if line.startswith(":"):
            return None

        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]

        if field == "event":
            self._event = value or None
        elif field == "data":
            self._data.append(value)
        elif field == "id" and "\0" not in value:
            self._last_event_id = value
        elif field == "retry" and value.isascii() and value.isdigit():
            self._retry = int(value)

        return None

    def _dispatch(self) -> ServerSentEvent | None:
        event_type = self._event
        data = self._data
        retry = self._retry

        self._event = None
        self._data = []
        self._retry = None

        if not data:
            return None

        return ServerSentEvent(
            event=event_type,
            data="\n".join(data),
            id=self._last_event_id,
            retry=retry,
        )


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
            yield from ServerSentEventDecoder().iter_bytes(response.iter_bytes())

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
            async for event in ServerSentEventDecoder().aiter_bytes(response.aiter_bytes()):
                yield event
