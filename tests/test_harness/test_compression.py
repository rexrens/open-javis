"""Tests for the compression middleware (snip + history cap)."""

from __future__ import annotations

from javis.engines.harness.compression import (
    HistoryCompressor,
    make_snip_listener,
)
from javis.engines.harness.core.contracts import (
    PostToolDecision,
    TextBlock,
    ToolExecutionResult,
)


def _result(text: str) -> ToolExecutionResult:
    return ToolExecutionResult(content=[TextBlock(text=text)])


def test_snip_truncates_long_output():
    listener = make_snip_listener(max_chars=100)
    long_text = "x" * 500
    decision = listener(None, _result(long_text), lambda: None)

    assert isinstance(decision, PostToolDecision)
    assert decision.content is not None
    joined = "".join(b.text for b in decision.content)
    assert len(joined) <= 100 + 60  # truncation + ellipsis marker
    assert "truncated by compression middleware" in joined
    assert joined.startswith("x" * 100)


def test_snip_passthrough_short_output():
    listener = make_snip_listener(max_chars=100)
    result = _result("short")
    assert listener(None, result, lambda: None) is None  # unchanged


def test_snip_non_text_blocks_kept():
    listener = make_snip_listener(max_chars=10)
    from javis.engines.harness.core.contracts import ToolResultBlock

    block = ToolResultBlock(tool_call_id="c1", content=(TextBlock(text="tool block"),))
    result = ToolExecutionResult(content=[block, TextBlock(text="z" * 50)])
    decision = listener(None, result, lambda: None)
    assert isinstance(decision, PostToolDecision)
    assert isinstance(decision.content[0], ToolResultBlock)  # non-text preserved


def test_history_compressor_keeps_last_n():
    compressor = HistoryCompressor(max_messages=3)
    from javis.engines.harness.core.contracts import UserMessage

    messages = [UserMessage.from_text(f"m{i}") for i in range(6)]
    kept = compressor(messages)
    assert len(kept) == 3
    assert kept[0].text == "m3" and kept[-1].text == "m5"


def test_history_compressor_never_leads_with_tool_message():
    compressor = HistoryCompressor(max_messages=4)
    from javis.engines.harness.core.contracts import (
        AssistantMessage,
        ToolResultMessage,
        UserMessage,
    )

    messages = [
        UserMessage.from_text("old"),
        AssistantMessage(content=(TextBlock(text="old reply"),)),
        UserMessage.from_text("new"),
        AssistantMessage(content=(TextBlock(text="new reply"),)),
        ToolResultMessage.for_call("c1", [TextBlock(text="result")]),
    ]
    kept = compressor(messages)
    assert len(kept) == 4
    # the leading tool message (whose call was trimmed) is dropped
    assert kept[0].role != "tool"


def test_history_compressor_small_history_passthrough():
    compressor = HistoryCompressor(max_messages=10)
    from javis.engines.harness.core.contracts import UserMessage

    messages = [UserMessage.from_text("a"), UserMessage.from_text("b")]
    assert compressor(messages) == messages
