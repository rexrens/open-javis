"""Tests for the dsh-style mock demo."""

from __future__ import annotations

import asyncio
from pathlib import Path

from examples.agentloop_demo.mock_dsh import DshRuntime
from examples.agentloop_demo.plugins.agents import AgentHandle, AgentsService
from examples.agentloop_demo.plugins.session import (
    DemoSessionService,
    Session,
    SessionStore,
)

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"


def test_derive_messages_folds_session_events() -> None:
    session = Session("t")
    session.append("user/message", {"message": {"role": "user", "content": "hi"}})
    session.append(
        "assistant/message",
        {"message": {"role": "assistant", "content": "ok"}},
    )
    session.append("tool/result", {"tool_call_id": "c1", "content": "result"})

    messages = session.derive_messages(system_prompt="sys")
    assert messages == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]


def test_unknown_event_type_rejected() -> None:
    class FakeCtx:
        def emit(self, _event: str, _payload: object = None) -> None:
            pass

    service = DemoSessionService(FakeCtx())
    service.create("t")
    try:
        service.append("t", "nope/event", {})
    except ValueError:
        pass
    else:
        raise AssertionError("unknown event type should be rejected")


def test_runtime_mounts_settings_and_runs_turn() -> None:
    async def run() -> None:
        async with DshRuntime(SETTINGS_PATH) as ctx:
            agents = ctx.get(AgentsService)
            handle = await agents.create(
                {"sessionId": "test-session", "cwd": str(SETTINGS_PATH.parent)}
            )
            assert isinstance(handle, AgentHandle)
            await handle.followup("hello")
            await handle.when_idle()
            assert handle.final_text

            session = ctx.get(SessionStore).get("test-session")
            assert any(event.type == "turn/end" for event in session.events)

    asyncio.run(run())
