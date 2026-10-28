"""Tests for the incremental Server-Sent Events transport."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

from javis.agent.model.transport import (
    ServerSentEvent,
    ServerSentEventDecoder,
)


def _sync_chunks(chunks: list[bytes]) -> Iterator[bytes]:
    yield from chunks


async def _async_chunks(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def test_decoder_handles_multiline_data_crlf_and_chunk_boundaries() -> None:
    decoder = ServerSentEventDecoder()
    chunks = [
        b": keep-alive\r",
        b"\n\r\nevent: delta\r\nid: 42\r\ndata: hel",
        b"lo\r\ndata: world\r\n\r\n",
    ]

    assert list(decoder.iter_bytes(_sync_chunks(chunks))) == [
        ServerSentEvent(event="delta", data="hello\nworld", id="42")
    ]


def test_decoder_handles_utf8_split_across_chunks() -> None:
    payload = "data: \u4f60\u597d\n\n".encode()

    events = list(ServerSentEventDecoder().iter_bytes(_sync_chunks([payload[:8], payload[8:]])))

    assert events == [ServerSentEvent(data="\u4f60\u597d")]


def test_decoder_ignores_incomplete_event_at_eof() -> None:
    assert list(ServerSentEventDecoder().iter_bytes(_sync_chunks([b"data: pending"]))) == []


def test_decoder_retains_last_event_id_and_only_valid_retry_values() -> None:
    chunks = [
        b"id: first\nretry: 1500\ndata: one\n\n",
        b"retry: invalid\ndata: two\n\n",
    ]

    assert list(ServerSentEventDecoder().iter_bytes(_sync_chunks(chunks))) == [
        ServerSentEvent(data="one", id="first", retry=1500),
        ServerSentEvent(data="two", id="first"),
    ]


def test_async_decoder_uses_the_same_decoding_rules() -> None:
    chunks = [b"event: update\n", b"data: async\n\n"]

    events = asyncio.run(_collect_async(chunks))

    assert events == [ServerSentEvent(event="update", data="async")]


async def _collect_async(chunks: list[bytes]) -> list[ServerSentEvent]:
    decoder = ServerSentEventDecoder()
    return [event async for event in decoder.aiter_bytes(_async_chunks(chunks))]
