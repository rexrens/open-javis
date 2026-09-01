"""Compression middleware for the harness engine.

Replaces the old corecoder ``ContextManager`` (128k-token LLM summarization)
with two pure-rule stages, both registered on the engine's inner loop
context as ordinary middleware — no core changes needed:

1. **Tool-output snip** — a ``tools/post-execute`` waterfall listener that
   truncates oversized tool results (default 8k chars) with an ellipsis
   marker, keeping the request well-formed.
2. **History cap** — the ``history_compressor`` hook the loop applies after
   ``derive_messages()`` and before building the next request: keeps the
   last N messages, dropping the oldest whole messages first (never leaving
   a leading ``tool`` message without its assistant tool-call).

LLM summarization (the old ``_summarize_old`` / ``_hard_collapse`` path) is
deliberately NOT implemented in v1 — it needs a second model call inside
the loop, which the dsh architecture would host as another middleware
listener; see ``SummarizeCompressor`` TODO below.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from javis.dsh.contracts import PostToolDecision, TextBlock, ToolExecutionResult

#: Default cap for a single tool result (chars). Matches the old corecoder's
#: 15_000-char bash truncation spirit, tighter because the request must stay
#: lean.
MAX_TOOL_OUTPUT_CHARS = 8_000

#: Default history cap (messages kept per request).
HISTORY_MAX_MESSAGES = 100


def make_snip_listener(max_chars: int = MAX_TOOL_OUTPUT_CHARS) -> Callable[..., Any]:
    """Build a ``tools/post-execute`` waterfall listener that truncates long
    tool outputs.

    Listener contract: ``(exec_input, result, next)`` — calling ``next()``
    lets the rest of the chain run (usually the no-op default); returning a
    :class:`PostToolDecision` replaces the committed content. Returns ``None``
    when the output fits, so the chain result stays a no-op.
    """

    def listener(_exec: Any, result: ToolExecutionResult, next: Callable[[], Any]) -> Any:
        next()
        text_blocks = [b for b in result.content if isinstance(b, TextBlock)]
        total = sum(len(b.text) for b in text_blocks)
        if total <= max_chars:
            return None
        new_content: list[Any] = []
        remaining = max_chars
        for block in result.content:
            if isinstance(block, TextBlock):
                if remaining <= 0:
                    continue
                if len(block.text) > remaining:
                    new_content.append(
                        TextBlock(
                            text=block.text[:remaining]
                            + f"\n... [truncated by compression middleware: "
                            f"{len(block.text)} chars]"
                        )
                    )
                    remaining = 0
                else:
                    new_content.append(block)
                    remaining -= len(block.text)
            else:
                new_content.append(block)
        return PostToolDecision(content=new_content)

    return listener


class HistoryCompressor:
    """``history_compressor`` hook: keep the last ``max_messages`` messages.

    The trim never leaves a leading ``tool`` message (a tool result without
    the assistant tool-call that produced it), which would make the OpenAI
    conversation invalid. Pure rule — no model calls, no recursion risk.
    """

    def __init__(self, max_messages: int = HISTORY_MAX_MESSAGES) -> None:
        self.max_messages = max(2, int(max_messages))

    def __call__(self, messages: list[Any]) -> list[Any]:
        if len(messages) <= self.max_messages:
            return messages
        kept = messages[-self.max_messages :]
        # Drop leading tool results whose assistant tool-call was trimmed away.
        while kept and getattr(kept[0], "role", "") == "tool":
            kept = kept[1:]
            if not kept:
                break
        return list(kept)


class SummarizeCompressor:
    """LLM-powered history summarization — v1 placeholder.

    TODO(compression-v2): implement the old corecoder ``_summarize_old`` /
    ``_hard_collapse`` semantics as an async middleware listener on the
    engine's loop context (a summarization request through the same LLM
    adapter, seeded by the request-error/request waterfall). Deliberately not
    implemented in v1 to avoid a recursive model call inside the hot path.
    """

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError(
            "SummarizeCompressor is a v1 placeholder; use HistoryCompressor "
            "and make_snip_listener for now"
        )


__all__ = [
    "HISTORY_MAX_MESSAGES",
    "MAX_TOOL_OUTPUT_CHARS",
    "HistoryCompressor",
    "SummarizeCompressor",
    "make_snip_listener",
]
