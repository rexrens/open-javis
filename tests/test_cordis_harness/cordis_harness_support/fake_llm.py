"""A scripted LLM adapter: deterministic turns with no network and no key."""

from __future__ import annotations

import json
import asyncio
from typing import Any

from harness.llm import LlmAdapter, LlmError
from harness.types import (
    block_end,
    block_start,
    finish_chunk,
    text_delta,
    tool_call_delta,
    usage_chunk,
)


def text_step(text: str, finish: str = "stop", usage: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """One step that streams ``text`` and stops."""
    chunks: list[dict[str, Any]] = [block_start(0, "text")]
    chunks += [text_delta(0, piece) for piece in _pieces(text)]
    chunks.append(block_end(0, {"type": "text", "text": text}))
    if usage:
        chunks.append(usage_chunk(usage))
    chunks.append(finish_chunk(finish))
    return chunks


def tool_step(call_id: str, name: str, arguments: dict[str, Any] | str, finish: str = "tool-calls") -> list[dict[str, Any]]:
    """One step that requests a single tool call."""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return [
        block_start(0, "tool-call"),
        tool_call_delta(0, call_id, raw[: len(raw) // 2], name),
        tool_call_delta(0, call_id, raw[len(raw) // 2 :]),
        block_end(0, {"type": "tool-call", "id": call_id, "name": name, "arguments": raw}),
        finish_chunk(finish),
    ]


def _pieces(text: str, size: int = 4) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


class ScriptedAdapter(LlmAdapter):
    """Yields the next scripted step per call and records every request.

    A step is either a list of chunks, a ``BaseException`` (raised on that
    call), or a list containing ``{"$sleep": seconds}`` control chunks so a
    test can cancel a turn while it is still streaming.
    """

    def __init__(self, steps: list[Any] | None = None):
        self.steps: list[Any] = list(steps or [])
        self.requests: list[Any] = []

    def push(self, step: Any) -> None:
        self.steps.append(step)

    async def stream(self, options: Any):
        self.requests.append(options)
        if not self.steps:
            raise LlmError("NO_SCRIPT", "the scripted adapter ran out of steps")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        for chunk in step:
            if "$sleep" in chunk:
                await asyncio.sleep(chunk["$sleep"])
                continue
            yield chunk


#: Used when neither the test nor ``CDH_FAKE_SCRIPT`` supplied a script.
DEFAULT_STEPS: list[Any] = [text_step("hello from the fake provider")]


def steps_from_env(raw: str | None) -> list[Any] | None:
    """Decode ``CDH_FAKE_SCRIPT`` (a JSON list of chunk lists) when present."""
    if not raw:
        return None
    return json.loads(raw)
