"""The agent loop: inbox, self-driven turns, events and cancellation.

The loop is driven by its own task, so tests observe it the way any other
consumer does: subscribe to events, hand it input, and wait until it settles.
"""

from __future__ import annotations

import asyncio

from harness.agent import Inbox, TARGET_NEXT_STEP, TARGET_NEXT_TURN
from harness.llm import LlmError
from cordis_harness_support import fake_llm_plugin
from cordis_harness_support.fake_llm import text_step, tool_step


class Recorder:
    """Collects the live events a consumer would render."""

    def __init__(self, ctx) -> None:
        self.streams: list[tuple] = []
        self.errors: list[tuple] = []
        self.statuses: list[str] = []
        self.steps: list[tuple] = []
        self.turns: list[tuple] = []
        self.session_events: list[str] = []
        self.tools: list[tuple] = []
        self._disposers = [
            ctx.on("agent/assistant-stream", lambda agent, chunk: self.streams.append((agent, chunk))),
            ctx.on("agent/error", lambda agent, turn, step, failure: self.errors.append((turn, step, failure))),
            ctx.on("agent/status", lambda agent, status: self.statuses.append(status)),
            ctx.on("agent/step-start", lambda agent, turn, step: self.steps.append((turn, step))),
            ctx.on("agent/turn-end", lambda agent, turn, finish: self.turns.append((turn, finish))),
            ctx.on("session/event", lambda session, event: self.session_events.append(event["type"])),
            ctx.on("tool/result", lambda call_id, name, text, is_error: self.tools.append((name, text, is_error))),
        ]

    @property
    def text(self) -> str:
        return "".join(
            chunk.get("text", "") for _, chunk in self.streams if chunk.get("type") == "text-delta"
        )

    def close(self) -> None:
        for dispose in self._disposers:
            dispose()
        self._disposers = []


async def _ready(mount, session_dir, **kwargs):
    """Mount, subscribe, and return ``(ctx, recorder, session, agent)``."""
    ctx, _ = await mount(session_dir=session_dir, **kwargs)
    recorder = Recorder(ctx)
    session = ctx.get("sessions").create(".", "fake", "fake-model")
    agent = ctx.get("agents").create(session)
    return ctx, recorder, session, agent


def test_inbox_claims_one_turn_message_plus_steering():
    inbox = Inbox()
    inbox.insert(TARGET_NEXT_TURN, "one")
    inbox.insert(TARGET_NEXT_TURN, "two")
    inbox.insert(TARGET_NEXT_STEP, "steer")
    assert inbox.claim_turn_input() == ["steer", "one"]
    assert inbox.pending() == {TARGET_NEXT_TURN: ["two"], TARGET_NEXT_STEP: []}
    assert inbox.has_turn_input() is True
    inbox.insert(TARGET_NEXT_STEP, "later")
    assert inbox.has_step_input() is True
    assert inbox.claim_step_input() == ["later"]
    assert inbox.has_step_input() is False


def test_inbox_rejects_unknown_targets():
    inbox = Inbox()
    try:
        inbox.insert("someday", "x")
    except ValueError as error:
        assert "unknown inbox target" in str(error)
    else:  # pragma: no cover - an unknown target must fail loudly
        raise AssertionError("unknown target must raise")


async def test_text_turn_streams_and_persists(mount, tmp_path):
    fake_llm_plugin.set_script([text_step("hello world")])
    ctx, recorder, session, agent = await _ready(mount, tmp_path / "s")

    agent.send("hi")
    await agent.wait_idle()

    assert recorder.text == "hello world"
    assert agent.result.text == "hello world"
    assert agent.result.finish == "stop"
    assert agent.status == "idle"
    assert [message["role"] for message in session.messages()] == ["user", "assistant"]
    assert [event["type"] for event in session.events()] == [
        "turn/start",
        "user/message",
        "assistant/message",
        "turn/end",
    ]
    # Both channels carried the same turn: durable log and live events.
    assert recorder.session_events == ["turn/start", "user/message", "assistant/message", "turn/end"]
    assert recorder.turns == [(1, "stop")]
    assert recorder.steps == [(1, 0)]
    assert recorder.statuses == ["working", "idle"]
    recorder.close()

    request = fake_llm_plugin.ADAPTER.requests[0]
    assert request.provider == "fake" and request.model == "fake-model"
    assert request.messages[0]["role"] == "system"
    assert "Working directory" in request.messages[0]["content"][0]["text"]
    assert {tool.name for tool in request.tools} == {"bash", "read_file", "write_file"}


async def test_tool_calls_run_and_feed_back_into_the_next_step(mount, tmp_path):
    fake_llm_plugin.set_script(
        [
            tool_step("call-1", "bash", {"command": "echo tool-output"}),
            text_step("done"),
        ]
    )
    ctx, recorder, session, agent = await _ready(mount, tmp_path / "s")
    agent.send("run it")
    await agent.wait_idle()

    assert [name for name, _, _ in recorder.tools] == ["bash"]
    assert "tool-output" in recorder.tools[0][1]
    assert agent.result.text == "done"
    assert [message["role"] for message in session.messages()] == ["user", "assistant", "tool", "assistant"]
    assert recorder.session_events.index("tool/call") < recorder.session_events.index("tool/result")

    second_request = fake_llm_plugin.ADAPTER.requests[1]
    assert second_request.messages[-1]["role"] == "tool"
    assert second_request.messages[-1]["content"][0]["toolCallId"] == "call-1"
    recorder.close()


async def test_approval_denial_is_reported_to_the_model(mount, tmp_path):
    fake_llm_plugin.set_script([tool_step("call-1", "bash", {"command": "rm -rf /"}), text_step("ok, I won't")])
    ctx, recorder, session, agent = await _ready(mount, tmp_path / "s")

    async def deny(call_id, definition, arguments):
        return False

    ctx.get("tools").set_approver(deny)
    agent.send("clean up")
    await agent.wait_idle()
    assert recorder.tools[0][2] is True
    assert "denied" in recorder.tools[0][1]
    recorder.close()


async def test_step_budget_stops_a_runaway_loop(mount, tmp_path):
    fake_llm_plugin.set_script([tool_step(f"call-{index}", "bash", {"command": "true"}) for index in range(5)])
    ctx, recorder, session, agent = await _ready(mount, tmp_path / "s", max_steps=2)
    agent.send("loop")
    await agent.wait_idle()

    assert agent.result.finish == "max-steps"
    assert len(recorder.steps) == 2
    assert recorder.errors[-1][2]["code"] == "MAX_STEPS"
    assert recorder.turns == [(1, "max-steps")]
    recorder.close()


async def test_provider_failures_surface_as_events_not_crashes(mount, tmp_path):
    fake_llm_plugin.set_script([LlmError("RATE_LIMIT", "slow down", 429)])
    ctx, recorder, session, agent = await _ready(mount, tmp_path / "s")
    agent.send("hi")
    await agent.wait_idle()

    assert recorder.errors == [(1, 0, {"code": "RATE_LIMIT", "message": "slow down", "status": 429})]
    assert agent.result.finish == "error"
    # The user message is still on the log, and the turn is closed.
    assert [message["role"] for message in session.messages()] == ["user"]
    assert [event["type"] for event in session.events()][-1] == "turn/end"
    recorder.close()


async def test_cancel_aborts_the_turn_but_keeps_the_agent(mount, tmp_path):
    fake_llm_plugin.set_script([[{"$sleep": 5}], text_step("late")])
    ctx, recorder, session, agent = await _ready(mount, tmp_path / "s")
    agent.send("hi")
    await asyncio.sleep(0.2)

    assert agent.cancel() is True
    await agent.wait_idle()
    assert agent.result.finish == "aborted"
    assert agent.status == "idle"
    assert [event["type"] for event in session.events()] == ["turn/start", "user/message", "turn/end"]
    assert recorder.turns == [(1, "aborted")]
    # The agent is still usable afterwards.
    agent.send("again")
    await agent.wait_idle()
    assert agent.cancel() is False
    recorder.close()


async def test_steering_rides_along_with_the_running_turn(mount, tmp_path):
    fake_llm_plugin.set_script([text_step("answered")])
    ctx, recorder, session, agent = await _ready(mount, tmp_path / "s")
    agent.inject("extra context")  # parked: steering alone does not wake the loop
    assert session.turns == 0
    agent.send("question")
    await agent.wait_idle()

    assert [message["role"] for message in session.messages()] == ["user", "user", "assistant"]
    assert session.turns == 1
    request = fake_llm_plugin.ADAPTER.requests[0]
    user_texts = [message["content"][0]["text"] for message in request.messages if message["role"] == "user"]
    assert user_texts == ["extra context", "question"]
    recorder.close()


async def test_each_queued_message_opens_its_own_turn(mount, tmp_path):
    fake_llm_plugin.set_script([text_step("one"), text_step("two")])
    ctx, recorder, session, agent = await _ready(mount, tmp_path / "s")
    agent.send("first")
    agent.send("second")
    await agent.wait_idle()

    assert session.turns == 2
    assert [message["role"] for message in session.messages()][0] == "user"
    assert recorder.turns == [(1, "stop"), (2, "stop")]
    recorder.close()


async def test_dispose_stops_the_loop(mount, tmp_path):
    fake_llm_plugin.set_script([text_step("hi")])
    ctx, recorder, session, agent = await _ready(mount, tmp_path / "s")
    await agent.dispose()
    assert agent.status == "disposed"
    try:
        agent.send("too late")
    except RuntimeError as error:
        assert "disposed" in str(error)
    else:  # pragma: no cover - a disposed agent must refuse input
        raise AssertionError("a disposed agent must refuse input")
    recorder.close()


async def test_services_close_every_agent_they_created(mount, tmp_path):
    fake_llm_plugin.set_script([text_step("hi")])
    ctx, recorder, session, agent = await _ready(mount, tmp_path / "s")
    agents = ctx.get("agents")
    assert agents.get(agent.id) is agent
    await agents.aclose()
    assert agents.agents() == []
    assert agent.status == "disposed"
    recorder.close()


async def test_agent_uses_the_session_route_and_can_override_it(mount, tmp_path):
    fake_llm_plugin.set_script([text_step("a")])
    ctx, _ = await mount(session_dir=tmp_path / "s")
    sessions = ctx.get("sessions")
    agents = ctx.get("agents")

    resumed = sessions.create(str(tmp_path), "other-provider", "stored-model")
    assert agents.create(resumed).provider == "other-provider"
    assert agents.create(resumed).model == "stored-model"
    assert agents.create(resumed, provider="fake", model="override").model == "override"
    assert len(agents.agents()) == 3
    await agents.aclose()
