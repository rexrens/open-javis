"""core/types data-contract invariants (dsh-aligned vocabulary)."""
from core import types as t


def test_session_event_types_include_compaction_family():
    assert {"compaction/start", "compaction/summary", "compaction/end"} <= set(t.SESSION_EVENT_TYPES)
    assert "user/message" in t.SESSION_EVENT_TYPES
    assert "agent/inbox/spliced" in t.SESSION_EVENT_TYPES


def test_call_config_equals():
    a = t.LlmCallConfig(provider="p", model="m")
    b = t.LlmCallConfig(provider="p", model="m")
    c = t.LlmCallConfig(provider="p", model="other")
    assert t.call_config_equals(a, b)
    assert not t.call_config_equals(a, c)


def test_finish_kinds():
    kinds = {
        t.StopFinish(): "stop",
        t.ToolCallsFinish(): "tool-calls",
        t.MaxTokensFinish(): "max-tokens",
        t.AbortedFinish(failure=t.LlmFailure(message="x", code="C")): "aborted",
        t.ErrorFinish(failure=t.LlmFailure(message="x", code="C")): "error",
    }
    for finish, expected in kinds.items():
        assert finish.kind == expected


def test_chunk_union_accepts_all_chunk_types():
    chunks = [
        t.BlockStartChunk(index=0, block_type="text"),
        t.TextDeltaChunk(index=0, text="hi"),
        t.BlockEndChunk(index=0, block=t.TextBlock(text="hi")),
        t.UsageChunk(usage=t.TokenUsage(input_tokens=1, output_tokens=1)),
        t.FinishChunk(reason=t.StopFinish()),
    ]
    for chunk in chunks:
        assert isinstance(chunk, t.StreamChunk)


def test_agent_loop_config_defaults():
    cfg = t.AgentLoopConfig()
    assert cfg.max_parallel_tool_calls == 4
    assert cfg.max_steps_per_turn == 20
    assert not hasattr(cfg, "history_compressor")  # javis 扩展已裁


def test_generate_options_constructible():
    opts = t.GenerateOptions(provider="p", model="m", messages=(t.UserMessage.from_text("hi"),))
    assert opts.system is None
