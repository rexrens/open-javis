"""Message, content-block and streaming vocabulary.

Mirrors the subset of ``@deepseek-ai/dsh-llm`` this harness needs. Content
blocks, messages and stream chunks are plain JSON-shaped dicts so a session
line round-trips without a codec; requests and failures are dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

ContentBlock = dict[str, Any]
Message = dict[str, Any]


def now_iso() -> str:
    """Current UTC time as a second-precision ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- content blocks ---------------------------------------------------------


def text_block(text: str) -> ContentBlock:
    return {"type": "text", "text": text}


def reasoning_block(text: str) -> ContentBlock:
    return {"type": "reasoning", "text": text}


def tool_call_block(call_id: str, name: str, arguments: str) -> ContentBlock:
    """A model-requested tool invocation; ``arguments`` stays a raw JSON string."""
    return {"type": "tool-call", "id": call_id, "name": name, "arguments": arguments}


def tool_result_block(call_id: str, content: list[ContentBlock], is_error: bool = False) -> ContentBlock:
    return {"type": "tool-result", "toolCallId": call_id, "content": content, "isError": is_error}


def blocks_text(blocks: list[ContentBlock]) -> str:
    """Join the text blocks of ``content`` (drops reasoning/tool blocks)."""
    return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")


# -- messages ---------------------------------------------------------------


def message(role: str, content: list[ContentBlock], **extra: Any) -> Message:
    return {"role": role, "content": content, **extra}


def system_message(text: str) -> Message:
    return message("system", [text_block(text)])


def user_message(text: str) -> Message:
    return message("user", [text_block(text)])


def assistant_message(blocks: list[ContentBlock]) -> Message:
    return message("assistant", blocks)


def tool_message(call_id: str, text: str, is_error: bool = False) -> Message:
    return message("tool", [tool_result_block(call_id, [text_block(text)], is_error)])


def message_text(msg: Message) -> str:
    return blocks_text(msg.get("content") or [])


def content_block_from_partial(partial: dict[str, Any]) -> ContentBlock | None:
    """Finalize one partially accumulated block, or ``None`` when it is empty.

    A tool-call block that never learned its id or name is dropped as
    incomplete (a malformed stream must not invent a call).
    """
    block_type = partial.get("blockType")
    if block_type in ("text", "reasoning"):
        text = partial.get("text") or ""
        if not text:
            return None
        return text_block(text) if block_type == "text" else reasoning_block(text)
    if block_type == "tool-call":
        call_id = partial.get("toolCallId")
        name = partial.get("toolCallName")
        if not call_id or not name:
            return None
        return tool_call_block(call_id, name, partial.get("toolCallArguments") or "")
    block = partial.get("block")
    return block if isinstance(block, dict) else None


# -- requests ---------------------------------------------------------------


@dataclass
class ToolSchema:
    """JSON-schema description of one tool, as sent to the model."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerateOptions:
    """One fully assembled model request."""

    provider: str
    model: str
    messages: list[Message]
    tools: list[ToolSchema] = field(default_factory=list)
    max_tokens: int | None = None
    reasoning_effort: str | None = None


# -- failures and termination ----------------------------------------------


@dataclass
class LlmFailure:
    """Stable machine-routing failure facts (never routed on message text)."""

    message: str
    code: str
    status: int | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"message": self.message, "code": self.code}
        if self.status is not None:
            data["status"] = self.status
        return data


@dataclass
class FinishReason:
    """Why a model response stopped."""

    kind: str  # stop | tool-calls | max-tokens | aborted | error
    failure: LlmFailure | None = None


#: Token accounting for one model call (counts are disjoint, as in dsh).
TokenUsage = dict[str, int]


# -- stream chunks ----------------------------------------------------------


def block_start(index: int, block_type: str) -> dict[str, Any]:
    return {"type": "block-start", "index": index, "blockType": block_type}


def text_delta(index: int, text: str) -> dict[str, Any]:
    return {"type": "text-delta", "index": index, "text": text}


def reasoning_delta(index: int, text: str) -> dict[str, Any]:
    return {"type": "reasoning-delta", "index": index, "text": text}


def tool_call_delta(index: int, call_id: str, arguments_delta: str, name: str | None = None) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "type": "tool-call-delta",
        "index": index,
        "id": call_id,
        "argumentsDelta": arguments_delta,
    }
    if name is not None:
        chunk["name"] = name
    return chunk


def block_end(index: int, block: ContentBlock) -> dict[str, Any]:
    return {"type": "block-end", "index": index, "block": block}


def usage_chunk(usage: TokenUsage) -> dict[str, Any]:
    return {"type": "usage", "usage": usage}


def finish_chunk(kind: str, failure: LlmFailure | None = None) -> dict[str, Any]:
    reason: dict[str, Any] = {"kind": kind}
    if failure is not None:
        reason["failure"] = failure.to_json()
    return {"type": "finish", "reason": reason}
