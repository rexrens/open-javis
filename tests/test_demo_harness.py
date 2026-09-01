"""Harness demo smoke tests (dsh-style loop on the Cordis plugin system).

End-to-end: each scenario boots the real composition (or a custom one),
drives the agent through its public API, and asserts on the durable session
log + the event hooks — the contract surface, not the implementation.

Run: ``uv run pytest tests/test_demo_harness.py -v``
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

DEMO_ROOT = Path(__file__).resolve().parents[1] / "examples" / "dsh_harness"
if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))

from cli import PROMPTS, final_assistant_text, seq_of, turn_end_reason
from cli import compose as compose_scenario
from mock_llm import MockLLM, MockResponse

from javis.cordis import Context, FiberState
from javis.cordis.registry import settle
from javis.dsh.contracts import (
    AgentCancelCause,
    Events,
    LlmFailure,
    PreStepReject,
    ToolCallBlock,
    UserMessage,
)
from javis.dsh.tools import Tool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def drive(agent: Any, prompt: str) -> None:
    agent.followup(UserMessage.from_text(prompt))
    await agent.when_idle()


def tool_result_text(session: Any, predicate: Any = None) -> list[str]:
    out = []
    for event in session.events_of("tool/result"):
        message = event.data["message"]
        block = message.content[0]
        from javis.dsh.contracts import TextBlock

        text = "".join(b.text for b in block.content if isinstance(b, TextBlock))
        if predicate is None or predicate(event.data, text):
            out.append(text)
    return out


async def compose_custom(script: list[MockResponse], *, max_parallel: int = 2) -> tuple[Context, Any, Any]:
    """Compose the same plugin set as cordis.yml, with a custom mock script."""
    import plugins.agent_loop_config as p_agent_loop
    import plugins.demo_tools as p_tools
    import plugins.driver as p_driver
    import plugins.middleware as p_middleware
    import plugins.observer as p_observer
    import plugins.system_prompt as p_system_prompt

    ctx = Context()
    ctx.plugin(p_agent_loop, {"max_parallel_tool_calls": max_parallel})
    ctx.plugin(p_system_prompt)
    ctx.provide("llm", MockLLM(script))
    ctx.plugin(p_tools)
    ctx.plugin(p_middleware)
    ctx.plugin(p_observer)
    ctx.plugin(p_driver)
    await settle(ctx)
    failed = [
        fiber
        for runtime in ctx.registry.values()
        for fiber in list(runtime.fibers)
        if fiber.state == FiberState.FAILED
    ]
    assert not failed, [fiber._error for fiber in failed]
    return ctx, ctx.get("agent"), ctx.get("session")


# ---------------------------------------------------------------------------
# The four cordis.yml scenarios
# ---------------------------------------------------------------------------


async def test_text_scenario() -> None:
    _ctx, agent, session = await compose_scenario("text")
    await drive(agent, PROMPTS["text"])

    assert turn_end_reason(session).kind == "completed"
    assert len(session.events_of("turn/start")) == len(session.events_of("turn/end")) == 1
    assert len(session.events_of("step/start")) == len(session.events_of("step/end"))
    assert "4" in final_assistant_text(session)

    # middleware rewrote the route; the adapter supplied maxTokens default
    header = session.request_header()
    assert header["config"]["model"] == "mock-mini-2026"
    assert header["config"]["maxTokens"] == 4096
    assert header["adapterDefaults"] == {"maxTokens": True}

    # request context carries the adapter's context window
    context = session.request_context()
    assert context["contextWindow"] == 8192

    in_tokens, out_tokens = session.usage_total()
    assert in_tokens > 0 and out_tokens > 0


async def test_tools_scenario() -> None:
    _ctx, agent, session = await compose_scenario("tools")
    await drive(agent, PROMPTS["tools"])

    assert turn_end_reason(session).kind == "completed"
    assert "Paris" in final_assistant_text(session)
    assert "Tokyo" in final_assistant_text(session)

    # three model-ordered calls, every one with a result
    calls = [event.data["name"] for event in session.events_of("tool/call")]
    assert calls == ["set_note", "weather", "weather"]
    assert len(session.events_of("tool/result")) == 3

    # exclusive barrier committed before the parallel pair started
    results = session.events_of("tool/result")
    assert "note saved" in tool_result_text(session)[0]
    assert any("Paris: 18C" in text for text in tool_result_text(session))
    assert any("Tokyo: 24C" in text for text in tool_result_text(session))

    # exclusive barrier: set_note's result committed before the pair started
    call_seqs = [event.seq for event in session.events_of("tool/call")]
    result_seqs = [event.seq for event in results]
    assert result_seqs[0] < call_seqs[1], "exclusive barrier committed before the parallel pair"
    # parallel pool (size 2): both weather calls started before either result
    assert max(call_seqs[1:]) < min(result_seqs[1:]), "the pair ran concurrently"


async def test_retry_scenario() -> None:
    ctx, agent, session = await compose_scenario("retry")
    await drive(agent, PROMPTS["retry"])

    assert turn_end_reason(session).kind == "completed"
    assert "Recovered" in final_assistant_text(session)

    # the request-error waterfall claimed recovery exactly once
    observed: list[str] = ctx.get("middleware-observed", strict=False) or []
    assert sum("retry (turn=" in line for line in observed) == 1

    # the failed attempt left chunks but no assistant message
    assert session.events_of("assistant/chunk")
    assert len(session.events_of("assistant/message")) == 1


async def test_steer_scenario() -> None:
    ctx, agent, session = await compose_scenario("steer")
    from mock_llm import steer_hook

    # wire the deterministic steer hook (like examples/dsh_harness/cli.py does)
    ctx.get("llm").on_tool_call = steer_hook(agent)
    await drive(agent, PROMPTS["steer"])

    assert turn_end_reason(session).kind == "completed"
    assert "Tokyo" in final_assistant_text(session)

    # the steering was claimed at the NEXT step boundary (after step 1 closed)
    steer_seq = seq_of(session, "user/message", lambda d: "also include Tokyo" in d["message"].text)
    assert steer_seq > 0
    assert steer_seq > seq_of(session, "step/end")
    # and two steps ran
    assert len(session.events_of("step/start")) == 2


# ---------------------------------------------------------------------------
# Contract semantics with custom scripts
# ---------------------------------------------------------------------------


async def test_concludes_turn() -> None:
    """A tool result with concludesTurn ends the turn right after committing."""
    script = [
        MockResponse(
            tool_calls=[ToolCallBlock(id="c1", name="end_session", arguments="{}")],
            usage=(8, 4),
        )
    ]
    _ctx, agent, session = await compose_custom(script)
    await drive(agent, "end it")

    assert turn_end_reason(session).kind == "completed"
    assert len(session.events_of("step/start")) == 1, "no second step after concludesTurn"
    assert any(
        event.data.get("concludesTurn") for event in session.events_of("tool/result")
    ), "result flagged concludesTurn"


async def test_pre_step_reject_blocks_turn() -> None:
    """An agent/pre-step waterfall veto ends the turn 'blocked' without a model call."""
    _ctx, agent, session = await compose_custom(
        [MockResponse(text="you should never see me", usage=(4, 4))]
    )

    def veto(_payload: Any, _next: Any) -> PreStepReject:
        return PreStepReject()

    agent.ctx.on(Events.AGENT_PRE_STEP, veto)
    await drive(agent, "hello")

    assert turn_end_reason(session).kind == "blocked"
    assert not session.events_of("assistant/chunk"), "no model call was spent"
    assert not session.events_of("assistant/message")


async def test_max_tokens_finish_is_sticky() -> None:
    script = [MockResponse(text="truncated an", max_tokens=True, usage=(8, 2048))]
    _ctx, agent, session = await compose_custom(script)
    await drive(agent, "long story")

    assert turn_end_reason(session).kind == "max-tokens"
    assert "truncated an" in final_assistant_text(session)


async def test_non_retryable_failure_ends_turn_with_error() -> None:
    script = [MockResponse(failure=LlmFailure(message="no such route", code="FATAL", status=404))]
    ctx, agent, session = await compose_custom(script)
    await drive(agent, "hello")

    assert turn_end_reason(session).kind == "error"
    assert not session.events_of("assistant/message")
    observer: Any = ctx.get("observer")
    assert any("agent error" in line for line in observer.lines)
    observed: list[str] = ctx.get("middleware-observed", strict=False) or []
    assert any("no recovery" in line for line in observed)


async def test_abort_synthesizes_result_for_skipped_calls() -> None:
    """Cancel mid-step: the unstarted parallel call gets a synthetic error result."""
    script = [
        MockResponse(
            tool_calls=[
                ToolCallBlock(id="c1", name="slow", arguments="{}"),
                ToolCallBlock(id="c2", name="now", arguments="{}"),
            ],
            usage=(8, 4),
        )
    ]
    # pool size 1 → "now" cannot start while "slow" runs
    _ctx, agent, session = await compose_custom(script, max_parallel=1)

    async def slow_body(_exec: Any) -> str:
        await asyncio.sleep(0.4)
        return "slow done"

    # register the slow tool on the live registry (test-only)
    registry = _ctx.get("tools")
    registry.register(Tool("slow", "sleeps a bit", body=slow_body), mode="parallel")

    agent.followup(UserMessage.from_text("run both"))
    # wait until the first tool call has started
    for _ in range(200):
        if session.events_of("tool/call"):
            break
        await asyncio.sleep(0.005)
    assert session.events_of("tool/call"), "the slow tool call should have started"
    agent.cancel(AgentCancelCause(kind="user"))
    await agent.when_idle()

    assert turn_end_reason(session).kind == "aborted"
    texts = tool_result_text(session)
    assert any("aborted before dispatch" in text for text in texts), "skipped call got a synthetic result"


async def test_additional_contexts_land_in_next_step() -> None:
    """tools/post-execute additionalContexts are staged into the next-step inbox."""
    script = [
        MockResponse(
            tool_calls=[ToolCallBlock(id="c1", name="now", arguments="{}")],
            usage=(8, 4),
        ),
        MockResponse(text="done with injected context", usage=(8, 4)),
    ]
    ctx, agent, session = await compose_custom(script)

    def add_context(_exec: Any, result: Any, _next: Any) -> Any:
        from javis.dsh.contracts import PostToolDecision

        return PostToolDecision(
            additional_contexts=(UserMessage.from_text("[injected by tools/post-execute]"),)
        )

    ctx.on(Events.TOOLS_POST_EXECUTE, add_context)
    await drive(agent, "what time?")

    assert turn_end_reason(session).kind == "completed"
    assert seq_of(session, "user/message", lambda d: "injected by tools/post-execute" in d["message"].text) > 0
