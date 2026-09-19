"""Stream vocabulary and the incremental assembler."""

from __future__ import annotations

from harness.assembler import BlockAssembler
from harness.types import (
    block_end,
    block_start,
    finish_chunk,
    reasoning_delta,
    text_delta,
    tool_call_delta,
    usage_chunk,
)


def test_text_and_reasoning_blocks_assemble_in_order():
    assembler = BlockAssembler()
    for chunk in [
        block_start(0, "reasoning"),
        reasoning_delta(0, "thinking…"),
        block_start(1, "text"),
        text_delta(1, "hello "),
        text_delta(1, "world"),
        usage_chunk({"inputTokens": 3, "outputTokens": 2}),
        finish_chunk("stop"),
    ]:
        assembler.push(chunk)
    assert assembler.blocks() == [
        {"type": "reasoning", "text": "thinking…"},
        {"type": "text", "text": "hello world"},
    ]
    assert assembler.usage == {"inputTokens": 3, "outputTokens": 2}
    assert assembler.finish_kind() == "stop"


def test_delta_only_stream_assembles_without_block_markers():
    assembler = BlockAssembler()
    assembler.push(text_delta(0, "a"))
    assembler.push(tool_call_delta(1, "call-1", '{"path":', "read_file"))
    assembler.push(tool_call_delta(1, "call-1", '"x.txt"}'))
    assembler.push(finish_chunk("tool-calls"))
    assert assembler.blocks() == [
        {"type": "text", "text": "a"},
        {"type": "tool-call", "id": "call-1", "name": "read_file", "arguments": '{"path":"x.txt"}'},
    ]
    assert assembler.finish_kind() == "tool-calls"


def test_block_end_wins_over_accumulated_deltas():
    assembler = BlockAssembler()
    assembler.push(block_start(0, "text"))
    assembler.push(text_delta(0, "partial"))
    assembler.push(block_end(0, {"type": "text", "text": "authoritative"}))
    assert assembler.blocks() == [{"type": "text", "text": "authoritative"}]


def test_empty_and_incomplete_blocks_are_dropped():
    assembler = BlockAssembler()
    assembler.push(block_start(0, "text"))
    assembler.push(text_delta(0, ""))
    assembler.push(block_start(1, "tool-call"))
    assembler.push(tool_call_delta(1, "", "{}"))
    assert assembler.blocks() == []
    assert assembler.has_content() is False


def test_failure_is_carried_on_the_finish_reason():
    assembler = BlockAssembler()
    assembler.push(finish_chunk("error"))
    assert assembler.finish_kind() == "error"
