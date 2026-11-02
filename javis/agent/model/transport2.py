from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterator
import httpx

class ServerSentEventTransport:
    def __init__(
        self,
        timeout: int = 5,
        verify_ssl: bool = True,
        proxy: str | None = None,

    ) -> None:
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.proxy = proxy
        self._decoder = ServerSentEventDecoder()

    def _iter_events(self, raw: Iterator[bytes])-> Iterator[ServerSentEvent]:
        for sse in self._decoder.iter_bytes(raw):
            yield sse

    async def _aiter_events(self, raw: AsyncIterator[bytes])-> AsyncIterator[ServerSentEvent]:
        async for sse in self._decoder.aiter_bytes(raw):
            yield sse

    def stream(self, url, headers:dict[str, str], body: dict[str, Any]) -> Iterator[ServerSentEvent]:
        with httpx.Client(timeout=self.timeout, verify=self.verify_ssl, proxy=self.proxy,) as client:
            with client.stream("POST", url, headers=headers, json=body) as response:
                response.raise_for_status()
                for sse in self._iter_events(response.iter_bytes()):
                    yield sse

    async def astream(self, url, headers:dict[str, str], body: dict[str, Any]) -> AsyncIterator[ServerSentEvent]:
        async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify_ssl, proxy=self.proxy,) as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                response.raise_for_status()
                async for sse in self._aiter_events(response.aiter_bytes()):
                    yield sse



@dataclass(slots=True)
class ServerSentEvent:
    """一个完整的 SSE 事件。"""
    event: str | None = None
    data: str | None = None
    id: str | None = None
    retry: int | None = None


class ServerSentEventDecoder:
    """SSE 解码器"""
    def __init__(self) -> None:
        self._event: str|None = None
        self._decoder = "utf-8"
        self._data: list[str] = []
        self._last_event_id: str|None = None
        self._retry: int | None = None

    def _iter_chunks(self, iterator: Iterator[bytes]) -> Iterator[bytes]:
        """Given an iterator that yields raw binary data, iterate over it and yield individual SSE chunks"""
        data = b""
        for chunk in iterator:
            for line in chunk.splitlines(keepends=True):
                data += line
                if data.endswith((b"\r\r", b"\n\n", b"\r\n\r\n")):
                    yield data
                    data = b""
        if data:
            yield data
    async def _aiter_chunks(self, iterator: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """Given an iterator that yields raw binary data, iterate over it and yield individual SSE chunks"""
        data = b""
        async for chunk in iterator:
            for line in chunk.splitlines(keepends=True):
                data += line
                if data.endswith((b"\r\r", b"\n\n", b"\r\n\r\n")):
                    yield data
                    data = b""
        if data:
            yield data

    def iter_bytes(self, iterator: Iterator[bytes]) -> Iterator[ServerSentEvent]:
        """Given an iterator that yields raw binary data, iterate over it & yield every event encountered"""
        for chunk in self._iter_chunks(iterator):
            # Split before decoding so splitlines() only uses \r and \n
            for raw_line in chunk.splitlines():
                line = raw_line.decode("utf-8")
                sse = self._decode(line)
                if sse:
                    yield sse

    async def aiter_bytes(self, iterator: AsyncIterator[bytes]) -> AsyncIterator[ServerSentEvent]:
        """Given an iterator that yields raw binary data, iterate over it & yield every event encountered"""
        async for chunk in self._aiter_chunks(iterator):
            # Split before decoding so splitlines() only uses \r and \n
            for raw_line in chunk.splitlines():
                line = raw_line.decode("utf-8")
                sse = self._decode(line)
                if sse:
                    yield sse

    def _decode(self, line: str) -> ServerSentEvent | None:

        if not line:
            if not self._event and not self._data and not self._last_event_id and self._retry is None:
                return None

            event = ServerSentEvent(
                event = self._event,
                data = "\n".join(self._data),
                id = self._last_event_id,
                retry = self._retry,
            )

            # NOTE: as per the SSE spec, do not reset last_event_id.
            self._event = None
            self._data = []
            self._retry = None

            return event

        # 注释 / 心跳的
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
