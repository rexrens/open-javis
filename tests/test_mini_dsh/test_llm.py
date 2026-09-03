"""LLM seam: BlockAssembler + normalized_stream + SystemPrompt."""
import pytest
from core import types as t
from core.llm import BlockAssembler, SystemPrompt, normalized_stream


def _chunks():
    return [
        t.BlockStartChunk(index=0, block_type="reasoning"),
        t.ReasoningDeltaChunk(index=0, text="think"),
        t.BlockEndChunk(index=0, block=t.ReasoningBlock(text="think")),
        t.BlockStartChunk(index=1, block_type="text"),
        t.TextDeltaChunk(index=1, text="hi"),
        t.BlockEndChunk(index=1, block=t.TextBlock(text="hi")),
        t.FinishChunk(reason=t.StopFinish()),
    ]


def test_block_assembler():
    assembler = BlockAssembler()
    for chunk in _chunks():
        assembler.push(chunk)
    assert isinstance(assembler.finish, t.StopFinish)
    assert [b.type for b in assembler.blocks] == ["reasoning", "text"]


@pytest.mark.asyncio
async def test_normalized_stream_error_finish():
    async def bad(_options):
        yield t.TextDeltaChunk(index=0, text="partial")
        raise t.LlmError("connection reset", "TRANSIENT")

    got = [c async for c in normalized_stream(bad(None), t.GenerateOptions(provider="p", model="m"))]
    assert isinstance(got[-1], t.FinishChunk)
    assert isinstance(got[-1].reason, t.ErrorFinish)
    assert got[-1].reason.failure.code == "TRANSIENT"


def test_system_prompt_assembly():
    from javis.cordis import Context

    ctx = Context()
    registry = type("R", (), {"schemas": lambda self: ()})()
    ctx.provide("tools", registry)
    sp = SystemPrompt(ctx, "You are mini.", cwd="/tmp", session_id="s1")
    assembly = sp.assemble()
    assert assembly.sections[0].kind == "persona"
    assert "You are mini." in sp.render_prompt(assembly)
