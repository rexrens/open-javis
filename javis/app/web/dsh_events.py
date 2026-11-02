"""Build dsh Session-event payloads from javis agent events.

Every payload here is shaped for the released V3 envelope the browser journal
validates: surface events carry ``surfaceOp``, and each event's ``data`` object
carries exactly the members the released disposition admits (no extra keys).
"""

from __future__ import annotations

import json
import time
from typing import Any

#: Event types the journal treats as model-visible surface nodes.
SURFACE_TYPES = frozenset(
    {"system/message", "user/message", "assistant/message", "tool/result"}
)

#: Wire format version this host emits.
SESSION_FORMAT_VERSION = 3

#: Provider identity stamped on assistant messages.
PROVIDER = "javis"


def now_ms() -> int:
    """Current wall-clock time in epoch milliseconds, as dsh session events use."""
    return int(time.time() * 1000)


def event_record(
    event_type: str,
    seq: int,
    data: dict[str, Any],
    *,
    time_ms: int | None = None,
) -> dict[str, Any]:
    """Wrap one payload in the browser-wire event envelope."""
    event: dict[str, Any] = {
        "type": event_type,
        "seq": seq,
        "time": now_ms() if time_ms is None else time_ms,
        "data": data,
    }
    if event_type in SURFACE_TYPES:
        event["surfaceOp"] = "append"
    return {"type": "event", "event": event}


def user_message_data(text: str, *, rpc_id: str | None = None) -> dict[str, Any]:
    """A human prompt as a user-role surface message.

    ``rpc_id`` is the prompt's request identity; the client retires its local
    submission echo when it observes a durable user message carrying it, so
    omitting it renders the same prompt twice.
    """
    source: dict[str, Any] = {"kind": "user"}
    if rpc_id:
        source["rpcId"] = rpc_id
    return {
        "id": f"msg_{seq_id()}",
        "role": "user",
        "content": [{"type": "text", "text": text}],
        "source": source,
    }


def assistant_message_data(
    turn: int,
    step: int,
    text: str,
    stream: list[dict[str, Any]],
    model: str,
    *,
    interrupted: bool = False,
    content: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A settled assistant message with its compact stream attached."""
    data: dict[str, Any] = {
        "turn": turn,
        "step": step,
        "message": {
            "id": f"msg_{seq_id()}",
            "role": "assistant",
            "content": content if content is not None else [{"type": "text", "text": text}],
            "source": {"kind": "model", "provider": PROVIDER, "model": model},
        },
        "stream": stream,
    }
    if interrupted:
        data["interrupted"] = True
    if usage:
        data["usage"] = usage
    return data


def token_usage_data(usage: object) -> dict[str, Any] | None:
    """Map javis's per-turn usage onto dsh's ``TokenUsage``.

    Returns None when the turn reported nothing, so the durable event omits the
    optional member instead of claiming a zero-token call.
    """
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    if input_tokens == 0 and output_tokens == 0:
        return None
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": input_tokens + output_tokens,
    }


def tool_result_message_data(call_id: str, output: str, is_error: bool) -> dict[str, Any]:
    """One tool outcome as the user-role message dsh pairs with its call."""
    block: dict[str, Any] = {
        "type": "tool-result",
        "toolCallId": call_id,
        "content": [{"type": "text", "text": output}],
    }
    if is_error:
        block["isError"] = True
    return {
        "id": f"msg_{seq_id()}",
        "role": "user",
        "content": [block],
        "source": {"kind": "tool", "callId": call_id},
    }


def tool_error_data() -> dict[str, str]:
    """Failure identity admitted alongside a failed tool result."""
    return {"name": "ToolError", "code": "TOOL_ERROR"}


def text_chunks_record(
    time0: int, times: list[int], texts: list[str], *, index: int = 0
) -> dict[str, Any]:
    """Pack accumulated text deltas into one compact stream record."""
    base = times[0] if times else time0
    return {
        "type": "text-chunks",
        "time0": base,
        "index": index,
        "dt": [value - base for value in times],
        "texts": list(texts),
    }


def reasoning_chunks_record(
    time0: int, times: list[int], texts: list[str], *, index: int = 0
) -> dict[str, Any]:
    """Pack accumulated reasoning deltas into one compact stream record."""
    base = times[0] if times else time0
    return {
        "type": "reasoning-chunks",
        "time0": base,
        "index": index,
        "dt": [value - base for value in times],
        "texts": list(texts),
    }


def text_delta_chunk(index: int, text: str) -> dict[str, Any]:
    """One live text delta in the dsh stream-chunk vocabulary."""
    return {"type": "text-delta", "index": index, "text": text}


def reasoning_delta_chunk(index: int, text: str) -> dict[str, Any]:
    """One live reasoning delta in the dsh stream-chunk vocabulary."""
    return {"type": "reasoning-delta", "index": index, "text": text}


def turn_end_reason(kind: str, *, message: str = "") -> dict[str, Any]:
    """One released ``turn/end`` reason payload."""
    if kind == "completed":
        return {"kind": "completed"}
    if kind == "aborted":
        return {"kind": "aborted", "reason": {"kind": "user"}}
    return {"kind": "error", "error": {"message": message or "turn failed", "code": "UNKNOWN"}}


def arguments_json(tool_input: dict[str, Any]) -> str:
    """The raw argument string a tool call carries."""
    try:
        return json.dumps(tool_input, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


_ID_COUNTER = 0


def seq_id() -> str:
    """Mint one short unique suffix for message and attempt identities."""
    global _ID_COUNTER
    _ID_COUNTER += 1
    return f"{int(time.time() * 1000):x}{_ID_COUNTER:04x}"
