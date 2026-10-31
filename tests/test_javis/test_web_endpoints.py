"""Session runtime and Remote endpoint behavior for the web host."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from javis.app.web.dsh_events import SURFACE_TYPES
from javis.app.web.endpoints import register_all
from javis.app.web.protocol import RemoteError
from javis.app.web.registry import EndpointRegistry
from javis.app.web.session_runtime import SessionRuntime
from javis.contracts.messages import ConversationMessage
from javis.contracts.types import (
    AgentEvent,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.session.session_storage import JavisSessionBackend
from tests.test_javis.fake_backend import FakeEngine


class _SlowEngine(FakeEngine):
    """FakeEngine that keeps the turn open so cancellation can be observed."""

    async def submit_message(
        self, prompt: str | ConversationMessage
    ) -> AsyncIterator[AgentEvent]:
        text = prompt.text if isinstance(prompt, ConversationMessage) else prompt
        yield AgentTextDelta(text=f"working on {text}")
        await asyncio.sleep(30)
        yield AgentTurnEnd(text="never reached")


class _ScriptedEngine(FakeEngine):
    """FakeEngine that plays an explicit event list (a float means "sleep")."""

    def __init__(self, script: list[Any]) -> None:
        super().__init__()
        self._script = script

    async def submit_message(
        self, prompt: str | ConversationMessage
    ) -> AsyncIterator[AgentEvent]:
        del prompt
        for item in self._script:
            if isinstance(item, float):
                await asyncio.sleep(item)
            else:
                yield item


class _Sink:
    """Collecting stream sink."""

    def __init__(self) -> None:
        self.values: list[Any] = []

    async def send(self, value: Any) -> None:
        self.values.append(value)


class _PermissionRecordingEngine(FakeEngine):
    """FakeEngine that records the permission hook the host installs."""

    def __init__(self) -> None:
        super().__init__()
        self.checker: Any = None

    def set_permission_checker(self, checker: Any) -> None:
        self.checker = checker


@pytest.fixture
def web_runtime(tmp_path, fake_engine_factory):
    """A session runtime whose harness is the deterministic test double."""
    fake_engine_factory()
    return SessionRuntime(cwd=str(tmp_path), workspace=str(tmp_path / "workspace"))


def _registry(runtime: SessionRuntime) -> EndpointRegistry:
    registry = EndpointRegistry()
    register_all(registry, runtime)
    return registry


async def _run_turn(runtime: SessionRuntime, text: str) -> Any:
    state = await runtime.create_session()
    await runtime.prompt(state, text)
    assert state.task is not None
    await state.task
    return state


def _events(state: Any) -> list[dict[str, Any]]:
    return [record["event"] for record in state.records]


async def test_turn_emits_the_dsh_event_scaffold(web_runtime: SessionRuntime):
    state = await _run_turn(web_runtime, "hello there")
    events = _events(state)
    assert [event["type"] for event in events] == [
        "turn/start",
        "user/message",
        "step/start",
        "assistant/message",
        "step/end",
        "turn/end",
    ]
    # dsh sequences are 0-based: the first event's seq is 0.
    assert [event["seq"] for event in events] == [0, 1, 2, 3, 4, 5]
    for event in events:
        assert event["time"] > 0
        if event["type"] in SURFACE_TYPES:
            assert event["surfaceOp"] == "append"
            assert "sourceEventSeqs" not in event
        else:
            assert "surfaceOp" not in event

    user = events[1]["data"]
    assert set(user) == {"id", "role", "content", "source"}
    assert user["role"] == "user"
    assert user["source"] == {"kind": "user"}
    assert user["content"] == [{"type": "text", "text": "hello there"}]

    assistant = events[3]["data"]
    assert set(assistant) == {"turn", "step", "message", "stream"}
    assert assistant["turn"] == 1
    assert assistant["step"] == 1
    message = assistant["message"]
    assert set(message) == {"id", "role", "content", "source"}
    assert message["role"] == "assistant"
    assert message["source"]["kind"] == "model"
    assert message["source"]["provider"] == "javis"
    assert assistant["stream"][0]["type"] == "text-chunks"
    assert assistant["stream"][0]["index"] == 0
    assert events[5]["data"] == {"turn": 1, "reason": {"kind": "completed"}}
    assert state.running is False


async def test_tool_turn_pairs_call_and_result(web_runtime: SessionRuntime):
    state = await _run_turn(web_runtime, "please use the tool")
    events = _events(state)
    types = [event["type"] for event in events]
    # One dsh step is one model call plus the tools it requested.
    assert types == [
        "turn/start",
        "user/message",
        "step/start",
        "assistant/message",
        "tool/call",
        "tool/result",
        "step/end",
        "step/start",
        "assistant/message",
        "step/end",
        "turn/end",
    ]
    steps = [
        event["data"]["step"]
        for event in events
        if event["type"] in {"step/start", "step/end"}
    ]
    assert steps == [1, 1, 2, 2]
    assert "tool/call" in types and "tool/result" in types
    call = events[types.index("tool/call")]
    result = events[types.index("tool/result")]
    assert set(call["data"]) == {"turn", "step", "callId", "name", "arguments"}
    assert call["data"]["name"] == "echo"
    assert call["data"]["arguments"] == '{"text": "please use the tool"}'
    assert set(result["data"]) == {"turn", "step", "message"}
    message = result["data"]["message"]
    assert set(message) == {"id", "role", "content", "source"}
    block = message["content"][0]
    assert block["type"] == "tool-result"
    assert block["toolCallId"] == call["data"]["callId"]
    assert message["source"] == {"kind": "tool", "callId": call["data"]["callId"]}
    assert types[-1] == "turn/end"


async def test_prompt_request_id_reaches_the_durable_user_message(
    web_runtime: SessionRuntime,
):
    """Without this echo the client never retires its local submission row."""
    state = await web_runtime.create_session()
    await web_runtime.prompt(state, "hello", request_id="req-echo-1")
    assert state.task is not None
    await state.task
    user = _events(state)[1]
    assert user["type"] == "user/message"
    assert user["data"]["source"] == {"kind": "user", "rpcId": "req-echo-1"}


async def test_replayed_history_omits_the_prompt_echo_id(web_runtime: SessionRuntime):
    """A restored message has no pending client echo to retire."""
    state = await web_runtime.create_session()
    await web_runtime.prompt(state, "first", request_id="req-1")
    assert state.task is not None
    await state.task
    session_id = state.session_id
    await web_runtime.aclose()

    resumed = SessionRuntime(cwd=web_runtime.cwd, workspace=web_runtime.workspace)
    restored = await resumed.ensure_session(session_id)
    user = _events(restored)[1]
    assert user["data"]["source"] == {"kind": "user"}


async def test_assistant_stream_frames_precede_the_settlement(
    tmp_path, fake_engine_factory
):
    fake_engine_factory()
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(tmp_path / "workspace"))
    state = await runtime.create_session()
    queue = runtime.subscribe(state)
    try:
        await runtime.prompt(state, "hi")
        assert state.task is not None
        await state.task
        frames: list[dict[str, Any]] = []
        while not queue.empty():
            frame = queue.get_nowait()
            if frame is not None:
                frames.append(frame)
    finally:
        runtime.unsubscribe(state, queue)

    frames = [frame for frame in frames if frame["type"] == "assistant-stream"]
    assert frames[0]["frame"]["type"] == "start"
    assert frames[0]["frame"]["revision"] == 1
    chunks = [frame for frame in frames if frame["frame"]["type"] == "chunk"]
    assert [frame["frame"]["index"] for frame in chunks] == list(range(len(chunks)))
    assert chunks[0]["frame"]["chunk"]["type"] == "text-delta"
    end = frames[-1]["frame"]
    assert end["type"] == "end"
    assert end["outcome"]["kind"] == "committed"
    assert end["outcome"]["eventType"] == "assistant/message"


async def test_follow_snapshot_then_live_records(web_runtime: SessionRuntime):
    state = await _run_turn(web_runtime, "hello")
    sink = _Sink()
    handler = _registry(web_runtime).stream_handler("session/follow")
    task = asyncio.create_task(
        handler({"sessionId": state.session_id}, sink)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    snapshot = sink.values[0]
    assert snapshot["type"] == "snapshot"
    assert snapshot["header"]["id"] == state.session_id
    assert snapshot["header"]["version"] == 3
    assert snapshot["header"]["isSeeded"] is False
    assert snapshot["cursor"] == state.seq
    assert snapshot["hasMore"] is False
    assert snapshot["projections"]["asOfSeq"] == state.seq
    assert snapshot["projections"]["values"]["modelSelection"]["next"]["provider"] == "javis"
    assert len(snapshot["records"]) == len(state.records)
    assert snapshot["records"][0]["event"]["type"] == "turn/start"


async def test_cancel_ends_the_turn_as_aborted(tmp_path, fake_engine_factory):
    fake_engine_factory(_SlowEngine())
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(tmp_path / "workspace"))
    state = await runtime.create_session()
    await runtime.prompt(state, "slow work")
    await asyncio.sleep(0.05)
    assert await runtime.cancel(state) is True
    events = _events(state)
    assert events[-1]["type"] == "turn/end"
    assert events[-1]["data"]["reason"] == {"kind": "aborted", "reason": {"kind": "user"}}
    assert any(event["type"] == "step/end" for event in events)
    assert state.running is False


async def test_prompt_rejects_empty_content(web_runtime: SessionRuntime):
    registry = _registry(web_runtime)
    state = await web_runtime.create_session()
    handler = registry.unary_handler("session/prompt")
    with pytest.raises(RemoteError) as excinfo:
        await handler({"sessionId": state.session_id, "content": [{"type": "text", "text": " "}]})
    assert excinfo.value.code == "gateway/bad-request"


async def test_missing_session_maps_to_session_not_found(web_runtime: SessionRuntime):
    registry = _registry(web_runtime)
    handler = registry.unary_handler("session/cancel")
    with pytest.raises(RemoteError) as excinfo:
        await handler({"sessionId": "does-not-exist"})
    assert excinfo.value.code == "session/not-found"


async def test_unknown_endpoint_is_a_structured_failure(web_runtime: SessionRuntime):
    registry = _registry(web_runtime)
    with pytest.raises(RemoteError) as excinfo:
        registry.unary_handler("terminal/create")
    assert excinfo.value.code == "gateway/unknown-endpoint"
    assert "terminal/create" in excinfo.value.message


async def test_permission_catalog_publishes_only_auto(web_runtime: SessionRuntime):
    registry = _registry(web_runtime)
    value = await registry.unary_handler("permissionPresets/catalog")({})
    assert value["options"] == [
        {
            "value": "auto",
            "name": "Auto",
            "description": "Run every tool call without asking (frozen for this javis build).",
        }
    ]


async def test_settings_are_read_only(web_runtime: SessionRuntime):
    registry = _registry(web_runtime)
    described = await registry.unary_handler("settings/describe")({})
    assert described == {"writable": False, "hasDocument": False, "namespaces": []}
    with pytest.raises(RemoteError) as excinfo:
        await registry.unary_handler("settings/mutate")({"namespace": "theme"})
    assert excinfo.value.code == "gateway/unknown-endpoint"


async def test_workspace_baseline_has_one_row(web_runtime: SessionRuntime):
    await web_runtime.create_session()
    registry = _registry(web_runtime)
    sink = _Sink()
    task = asyncio.create_task(registry.stream_handler("workspace/follow")({}, sink))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    frame = sink.values[0]
    assert frame["type"] == "baseline"
    items = frame["value"]["items"]
    assert len(items) == 1
    assert set(items[0]) == {
        "workspaceId",
        "path",
        "title",
        "sessionIds",
        "createdAt",
        "updatedAt",
    }
    assert frame["value"]["archivedSessionIds"] == []


async def test_events_stream_opens_with_ready(web_runtime: SessionRuntime):
    registry = _registry(web_runtime)
    sink = _Sink()
    task = asyncio.create_task(registry.stream_handler("$events")({}, sink))
    await asyncio.sleep(0.05)
    state = await web_runtime.create_session()
    await web_runtime.prompt(state, "hi")
    assert state.task is not None
    await state.task
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    ready = sink.values[0]
    assert ready["type"] == "ready"
    assert ready["host"]["home"] == web_runtime.cwd
    emits = [value for value in sink.values if value["type"] == "emit"]
    assert [value["event"] for value in emits].count("api-session/status") >= 2


async def test_session_list_includes_created_session(web_runtime: SessionRuntime):
    state = await web_runtime.create_session()
    value = await _registry(web_runtime).unary_handler("session/list")({})
    rows = {row["sessionId"]: row for row in value["items"]}
    assert state.session_id in rows
    row = rows[state.session_id]
    assert row["blank"] is True
    assert row["running"] is False
    assert row["cwd"] == web_runtime.cwd


async def test_web_host_installs_the_auto_permission_checker(tmp_path, fake_engine_factory):
    """The published Auto preset must match what the harness is told."""
    engine = _PermissionRecordingEngine()
    fake_engine_factory(engine)
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(tmp_path / "workspace"))
    await runtime.create_session()
    assert engine.checker is not None
    assert await engine.checker("bash", {"command": "ls"}) == "allow"


async def test_blank_session_identity_resolves_to_one_default_session(
    web_runtime: SessionRuntime,
):
    registry = _registry(web_runtime)
    listed = await registry.unary_handler("session/list")({})
    assert len(listed["items"]) == 1
    session_id = listed["items"][0]["sessionId"]

    handler = registry.unary_handler("session/page")
    blank = await handler({"sessionId": "", "maxMessages": 10})
    explicit = await handler({"sessionId": session_id, "maxMessages": 10})
    assert blank == explicit

    accepted = await registry.unary_handler("session/prompt")(
        {
            "sessionId": "",
            "content": [{"type": "text", "text": "hello from a blank composer"}],
        }
    )
    assert accepted == {"accepted": True}
    state = await web_runtime.ensure_default_session()
    assert state.task is not None
    await state.task
    assert state.records[-1]["event"]["type"] == "turn/end"


async def test_wire_shaped_request_arguments_are_unwrapped(web_runtime: SessionRuntime):
    """The real client sends `{"request": {...}}`, keyed by Host parameter name."""
    registry = _registry(web_runtime)
    created = await registry.unary_handler("session/create")({"request": {"cwd": web_runtime.cwd}})
    session_id = created["sessionId"]

    accepted = await registry.unary_handler("session/prompt")(
        {
            "request": {
                "requestId": "rq-1",
                "sessionId": session_id,
                "mode": "queue",
                "content": [{"type": "text", "text": "wire shaped"}],
            }
        }
    )
    assert accepted == {"accepted": True}
    state = await web_runtime.ensure_session(session_id)
    assert state.task is not None
    await state.task
    assert state.records[-1]["event"]["type"] == "turn/end"

    listed = await registry.unary_handler("session/list")({"_request": {}})
    assert any(row["sessionId"] == session_id for row in listed["items"])


async def test_follow_opted_into_assistant_stream_always_opens_with_a_baseline(
    web_runtime: SessionRuntime,
):
    state = await web_runtime.create_session()
    opt_in = web_runtime.snapshot_frame(state, assistant_stream=True)
    # An idle session announces the revision its next attempt will use.
    assert opt_in["assistantStream"] == {"revision": 1}
    assert "activeAttempt" not in opt_in["assistantStream"]

    plain = web_runtime.snapshot_frame(state)
    assert "assistantStream" not in plain

    handler = _registry(web_runtime).stream_handler("session/follow")
    sink = _Sink()
    task = asyncio.create_task(
        handler({"request": {"sessionId": state.session_id, "assistantStream": True}}, sink)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sink.values[0]["assistantStream"] == {"revision": 1}


async def test_brand_new_session_opens_at_cursor_minus_one(web_runtime: SessionRuntime):
    """An empty log's cursor is -1; a 0 cursor claims an event that does not exist."""
    state = await web_runtime.create_session()
    assert state.seq == -1
    snapshot = web_runtime.snapshot_frame(state, assistant_stream=True)
    assert snapshot["cursor"] == -1
    assert snapshot["records"] == []
    assert snapshot["hasMore"] is False
    # `cursor` may be -1 (empty log) but `asOfSeq` is a SessionSeq: never negative.
    assert snapshot["projections"]["asOfSeq"] == 0
    control = web_runtime.control_baseline()["projections"][state.session_id]
    assert control["asOfSeq"] == 0
    assert web_runtime.projection_values(state)["modelSelection"]["next"]["model"]

    page = await _registry(web_runtime).unary_handler("session/page")(
        {"request": {"sessionId": state.session_id, "throughSeq": -1}}
    )
    assert page == {"records": [], "hasMore": False}

    # The first real event still lands on seq 0, so the next cursor is 0.
    await web_runtime.prompt(state, "first")
    assert state.task is not None
    await state.task
    assert state.seq == 5


async def test_opening_baseline_carries_stream_records_not_raw_chunks(
    tmp_path, fake_engine_factory
):
    """The opening baseline announces the revision the next live frame carries.

    A revision that jumps between the baseline and the following ``start`` frame
    reads as a gap, which makes the client abandon the generation.
    """
    fake_engine_factory(_SlowEngine())
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(tmp_path / "workspace"))
    state = await runtime.create_session()
    queue = runtime.subscribe(state)
    try:
        idle = runtime.snapshot_frame(state, assistant_stream=True)["assistantStream"]
        assert idle == {"revision": 1}
        await runtime.prompt(state, "hi")
        # Wait until the attempt has streamed at least one chunk, so the
        # opening baseline has accumulated records to carry.
        while True:
            frame = await queue.get()
            if (
                isinstance(frame, dict)
                and frame.get("type") == "assistant-stream"
                and frame["frame"]["type"] == "start"
            ):
                started = frame["frame"]
                break
        snapshot = runtime.snapshot_frame(state, assistant_stream=True)
        baseline = snapshot["assistantStream"]
    finally:
        await runtime.cancel(state)
        runtime.unsubscribe(state, queue)

    # The live frame and the baseline agree, so the client never sees a gap.
    assert started["revision"] == 1
    assert baseline == {"revision": 1}
    assert "activeAttempt" not in baseline


async def test_model_catalog_matches_the_client_contract(web_runtime: SessionRuntime):
    registry = _registry(web_runtime)
    catalog = await registry.unary_handler("session/modelCatalog")({"request": {}})
    assert set(catalog) == {"default", "routableProviders", "groups", "failures"}
    assert catalog["default"] == {"provider": "javis", "model": "deepseek-chat"}
    assert catalog["routableProviders"] == ["javis"]
    assert catalog["groups"][0]["models"][0]["id"] == "deepseek-chat"
    assert catalog["failures"] == []

    state = await web_runtime.create_session()
    selected = await registry.unary_handler("session/selectModel")(
        {"request": {"sessionId": state.session_id, "provider": "javis", "model": "deepseek-chat"}}
    )
    assert selected == {"selected": {"provider": "javis", "model": "deepseek-chat"}}


async def test_model_selection_projection_reaches_control_followers(
    web_runtime: SessionRuntime,
):
    """The composer's model seat reads this projection; without it it never loads."""
    state = await web_runtime.create_session()
    baseline = web_runtime.control_baseline()
    entry = baseline["projections"][state.session_id]
    assert entry["asOfSeq"] == max(state.seq, 0)
    assert entry["values"]["modelSelection"]["next"]["model"] == state.model

    queue = web_runtime.subscribe_control()
    try:
        await _registry(web_runtime).unary_handler("session/selectModel")(
            {
                "request": {
                    "sessionId": state.session_id,
                    "provider": "javis",
                    "model": "deepseek-reasoner",
                }
            }
        )
        update = await asyncio.wait_for(queue.get(), timeout=1)
    finally:
        web_runtime.unsubscribe_control(queue)
    assert update["type"] == "projection"
    assert update["key"] == "modelSelection"
    assert update["value"]["next"] == {"provider": "javis", "model": "deepseek-reasoner"}


async def test_projection_updates_never_carry_a_negative_watermark(
    web_runtime: SessionRuntime,
):
    """A `-1` (empty-log) cursor is not a legal SessionSeq on a projection frame."""
    state = await web_runtime.create_session()
    assert state.seq == -1
    queue = web_runtime.subscribe_control()
    try:
        web_runtime.emit_model_selection(state)
        update = await asyncio.wait_for(queue.get(), timeout=1)
    finally:
        web_runtime.unsubscribe_control(queue)
    assert update["seq"] == 0
    assert update["key"] == "modelSelection"


async def test_session_title_projection_replaces_the_workspace_name(
    web_runtime: SessionRuntime,
):
    """Without the title projection every sidebar row falls back to the workspace name."""
    state = await web_runtime.create_session()
    assert web_runtime.projection_values(state)["title"] is None

    queue = web_runtime.subscribe_control()
    try:
        await web_runtime.prompt(state, "帮我重构 session runtime\n第二行不进标题")
        update = await asyncio.wait_for(queue.get(), timeout=1)
        assert state.task is not None
        await state.task
    finally:
        web_runtime.unsubscribe_control(queue)

    assert update["type"] == "projection"
    assert update["key"] == "title"
    assert update["value"] == "帮我重构 session runtime"
    assert web_runtime.projection_values(state)["title"] == "帮我重构 session runtime"
    row = next(row for row in web_runtime.list_rows() if row["sessionId"] == state.session_id)
    assert row["projections"]["values"]["title"] == "帮我重构 session runtime"


async def test_stored_sessions_expose_their_summary_as_a_title(tmp_path, fake_engine_factory):
    fake_engine_factory()
    workspace = tmp_path / "workspace"
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(workspace))
    state = await runtime.create_session()
    await runtime.prompt(state, "第一个会话的标题")
    assert state.task is not None
    await state.task
    await runtime.aclose()

    reopened = SessionRuntime(cwd=str(tmp_path), workspace=str(workspace))
    rows = reopened.list_rows()
    assert rows[0]["projections"]["values"]["title"] == "第一个会话的标题"


async def test_resume_rebuilds_tool_rounds_as_advertised_calls(
    tmp_path, fake_engine_factory
):
    """A tool result is not a new turn, and its call must be advertised."""
    from javis.contracts.messages import TextBlock, ToolResultBlock, ToolUseBlock
    from javis.contracts.usage import UsageSnapshot

    fake_engine_factory()
    workspace = tmp_path / "workspace"
    backend = JavisSessionBackend(workspace)
    backend.save_snapshot(
        cwd=str(tmp_path),
        model="test-model",
        system_prompt="s",
        usage=UsageSnapshot(),
        session_id="resumed-tools",
        # The timing journal the web host writes: user, assistant(+tool use),
        # tool result, final assistant.
        tool_metadata={"web_message_times": [1000, 2000, 5000, 7000]},
        messages=[
            ConversationMessage(role="user", content=[TextBlock(text="你好")]),
            ConversationMessage(
                role="assistant",
                content=[
                    TextBlock(text="我先看下目录。"),
                    ToolUseBlock(id="call-1", name="bash", input={"command": "ls"}),
                ],
            ),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="call-1", content="README.md\nsrc", is_error=False
                    )
                ],
            ),
            ConversationMessage(role="assistant", content=[TextBlock(text="看完了。")]),
        ],
    )

    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(workspace))
    state = await runtime.ensure_session("resumed-tools")
    events = _events(state)
    assert [event["type"] for event in events] == [
        "turn/start",
        "user/message",
        "step/start",
        "assistant/message",
        "tool/call",
        "tool/result",
        "step/end",
        "step/start",
        "assistant/message",
        "step/end",
        "turn/end",
    ]
    coordinates = [
        (event["data"].get("turn"), event["data"].get("step"))
        for event in events
        if event["type"] in {"step/start", "step/end", "tool/call", "tool/result"}
    ]
    # Everything stays inside turn 1; the second model call is step 2.
    assert coordinates == [(1, 1)] * 4 + [(1, 2)] * 2

    assistant = events[3]["data"]["message"]
    assert assistant["content"][0] == {"type": "text", "text": "我先看下目录。"}
    assert assistant["content"][1] == {
        "type": "tool-call",
        "id": "call-1",
        "name": "bash",
        "arguments": '{"command": "ls"}',
    }
    assert events[4]["data"]["callId"] == "call-1"
    assert events[5]["data"]["message"]["source"] == {"kind": "tool", "callId": "call-1"}
    assert events[-1]["data"]["reason"] == {"kind": "completed"}
    assert (state.turn, state.step) == (1, 2)
    # Durations replay from the journal: the tool ran 3s inside a 6s turn.
    times_by_type = {event["type"]: event["time"] for event in events if event["type"] != "turn/start"}
    assert events[0]["time"] == 1000
    assert times_by_type["user/message"] == 1000
    assert times_by_type["assistant/message"] == 7000
    assert times_by_type["tool/call"] == 2000
    assert times_by_type["tool/result"] == 5000
    assert times_by_type["turn/end"] == 7000


async def test_resume_replays_real_message_times(tmp_path, fake_engine_factory):
    """Durations come from the persisted per-message journal, not from 'now'."""
    fake_engine_factory()
    workspace = tmp_path / "workspace"
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(workspace))
    state = await runtime.create_session()
    await runtime.prompt(state, "please use the tool")
    assert state.task is not None
    await state.task
    times = list(state.message_times)
    # The test double mirrors one user prompt and one reply; the real harness
    # also mirrors the assistant's tool-use message and the tool result.
    assert len(times) == 4
    assert times == sorted(times)
    session_id = state.session_id
    await runtime.aclose()

    resumed = SessionRuntime(cwd=str(tmp_path), workspace=str(workspace))
    restored = await resumed.ensure_session(session_id)
    assert restored.message_times == times
    events = _events(restored)
    assert [event["type"] for event in events] == [
        "turn/start",
        "user/message",
        "step/start",
        "assistant/message",
        "step/end",
        "turn/end",
    ]
    assert events[1]["time"] == times[0]
    assert events[3]["time"] == times[1]
    # No fabricated token timing survives a resume.
    assert events[3]["data"]["stream"] == []


async def test_snapshot_without_timing_falls_back_to_creation_time(
    tmp_path, fake_engine_factory
):
    from javis.contracts.messages import TextBlock
    from javis.contracts.usage import UsageSnapshot

    fake_engine_factory()
    workspace = tmp_path / "workspace"
    backend = JavisSessionBackend(workspace)
    backend.save_snapshot(
        cwd=str(tmp_path),
        model="test-model",
        system_prompt="s",
        usage=UsageSnapshot(),
        session_id="legacy-session",
        messages=[
            ConversationMessage(role="user", content=[TextBlock(text="old prompt")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="old reply")]),
        ],
    )
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(workspace))
    state = await runtime.ensure_session("legacy-session")
    events = _events(state)
    assert state.message_times == []
    assert {event["time"] for event in events} == {state.created_at}


async def test_textless_tool_round_still_advertises_its_call(
    tmp_path, fake_engine_factory
):
    """dsh rejects a tool/call no assistant message advertised."""
    fake_engine_factory(
        _ScriptedEngine(
            [
                AgentToolCallStart(tool_name="bash", tool_input={"command": "ls"}),
                AgentToolCallResult(tool_name="bash", output="ok"),
                AgentTurnEnd(text="done"),
            ]
        )
    )
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(tmp_path / "workspace"))
    state = await runtime.create_session()
    await runtime.prompt(state, "不用说话，直接跑")
    assert state.task is not None
    await state.task
    events = _events(state)
    types = [event["type"] for event in events]
    assert types[:5] == [
        "turn/start",
        "user/message",
        "step/start",
        "assistant/message",
        "tool/call",
    ]
    advertised = events[3]["data"]["message"]["content"]
    assert advertised == [
        {
            "type": "tool-call",
            "id": events[4]["data"]["callId"],
            "name": "bash",
            "arguments": '{"command": "ls"}',
        }
    ]
    assert events[3]["data"]["turn"] == events[4]["data"]["turn"] == 1
    assert events[3]["data"]["step"] == events[4]["data"]["step"] == 1
    assert events[4]["data"]["arguments"] == '{"command": "ls"}'


async def test_aborted_turn_closes_its_pending_tool_call(tmp_path, fake_engine_factory):
    """A step/end or turn/end with an unresolved call is a contract violation."""
    fake_engine_factory(
        _ScriptedEngine(
            [
                AgentToolCallStart(tool_name="bash", tool_input={"command": "sleep 30"}),
                30.0,
            ]
        )
    )
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(tmp_path / "workspace"))
    state = await runtime.create_session()
    await runtime.prompt(state, "跑个长命令")
    await asyncio.sleep(0.05)
    await runtime.cancel(state)
    events = _events(state)
    types = [event["type"] for event in events]
    call_id = events[types.index("tool/call")]["data"]["callId"]
    result = events[types.index("tool/result")]["data"]
    assert result["message"]["content"][0]["toolCallId"] == call_id
    assert result["message"]["content"][0]["isError"] is True
    assert result["error"] == {"name": "ToolError", "code": "TOOL_ERROR"}
    assert types.index("tool/result") < types.index("step/end") < types.index("turn/end")
    assert events[-1]["data"]["reason"] == {"kind": "aborted", "reason": {"kind": "user"}}
    assert state.pending_calls == {}


async def test_turn_usage_lands_on_the_final_assistant_message(
    tmp_path, fake_engine_factory
):
    from javis.contracts.usage import UsageSnapshot

    fake_engine_factory(
        _ScriptedEngine(
            [
                AgentTextDelta(text="hi"),
                AgentTurnEnd(text="hi", usage=UsageSnapshot(input_tokens=120, output_tokens=8)),
            ]
        )
    )
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(tmp_path / "workspace"))
    state = await runtime.create_session()
    await runtime.prompt(state, "hello")
    assert state.task is not None
    await state.task
    assistant = next(
        event for event in _events(state) if event["type"] == "assistant/message"
    )
    assert assistant["data"]["usage"] == {
        "inputTokens": 120,
        "outputTokens": 8,
        "totalTokens": 128,
    }


async def test_reasoning_and_text_use_separate_content_blocks(
    tmp_path, fake_engine_factory
):
    """Live chunks are placed by index, so thinking and answer must not share one."""
    from javis.contracts.types import AgentReasoningDelta

    fake_engine_factory(
        _ScriptedEngine(
            [
                AgentReasoningDelta(text="先想一下："),
                AgentReasoningDelta(text="需要一句话"),
                AgentTextDelta(text="答案是"),
                AgentTextDelta(text="42。"),
                AgentTurnEnd(text="答案是42。"),
            ]
        )
    )
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(tmp_path / "workspace"))
    state = await runtime.create_session()
    queue = runtime.subscribe(state)
    try:
        await runtime.prompt(state, "想想再答")
        assert state.task is not None
        await state.task
        frames = []
        while not queue.empty():
            item = queue.get_nowait()
            if isinstance(item, dict) and item.get("type") == "assistant-stream":
                frames.append(item["frame"])
    finally:
        runtime.unsubscribe(state, queue)

    chunks = [frame for frame in frames if frame["type"] == "chunk"]
    reasoning_indices = {
        frame["chunk"]["index"]
        for frame in chunks
        if frame["chunk"]["type"] == "reasoning-delta"
    }
    text_indices = {
        frame["chunk"]["index"] for frame in chunks if frame["chunk"]["type"] == "text-delta"
    }
    assert reasoning_indices == {0}
    assert text_indices == {1}
    assert not (reasoning_indices & text_indices)

    settled = next(event for event in _events(state) if event["type"] == "assistant/message")
    blocks = settled["data"]["message"]["content"]
    assert blocks == [
        {"type": "reasoning", "text": "先想一下：需要一句话"},
        {"type": "text", "text": "答案是42。"},
    ]
    records = settled["data"]["stream"]
    assert [record["type"] for record in records] == ["reasoning-chunks", "text-chunks"]
    assert [record["index"] for record in records] == [0, 1]
    assert records[0]["texts"] == ["先想一下：", "需要一句话"]
    assert records[1]["texts"] == ["答案是", "42。"]


async def test_fork_copies_history_and_can_anchor_at_a_turn(
    tmp_path, fake_engine_factory
):
    fake_engine_factory()
    workspace = tmp_path / "workspace"
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(workspace))
    source = await runtime.create_session()
    await runtime.prompt(source, "第一轮")
    assert source.task is not None
    await source.task
    first_turn_end = source.seq
    await runtime.prompt(source, "第二轮")
    assert source.task is not None
    await source.task

    registry = _registry(runtime)
    whole = await registry.unary_handler("session/fork")({"request": {"sessionId": source.session_id}})
    forked = await runtime.ensure_session(whole["sessionId"])
    assert forked.session_id != source.session_id
    assert len(forked.bundle.engine.messages) == len(source.bundle.engine.messages)
    assert forked.records[-1]["event"]["type"] == "turn/end"

    anchored = await registry.unary_handler("session/fork")(
        {"request": {"sessionId": source.session_id, "atSeq": first_turn_end}}
    )
    cut = await runtime.ensure_session(anchored["sessionId"])
    assert len(cut.bundle.engine.messages) == 2
    assert cut.records[-1]["event"]["type"] == "turn/end"
    # Both forks are persisted, so they survive a restart.
    stored = {row["session_id"] for row in runtime.stored_sessions()}
    assert {whole["sessionId"], anchored["sessionId"]} <= stored


async def test_workspace_registration_and_selection(tmp_path, fake_engine_factory):
    fake_engine_factory()
    workspace = tmp_path / "workspace"
    other = tmp_path / "另一个项目"
    other.mkdir()
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(workspace))
    registry = _registry(runtime)

    created = await registry.unary_handler("workspace/create")({"request": {"path": str(other)}})
    assert created["created"] is True
    assert created["workspace"]["path"] == str(other)
    again = await registry.unary_handler("workspace/create")({"request": {"path": str(other)}})
    assert again["created"] is False

    baseline = runtime.workspace_rows()
    assert [row["path"] for row in baseline] == [str(tmp_path), str(other)]

    session = await registry.unary_handler("session/create")(
        {"request": {"workspaceId": created["workspace"]["workspaceId"]}}
    )
    state = await runtime.ensure_session(session["sessionId"])
    assert state.cwd == str(other)

    with pytest.raises(RemoteError) as excinfo:
        await registry.unary_handler("workspace/create")({"request": {"path": str(tmp_path / "nope")}})
    assert excinfo.value.code == "workspace/invalid-path"


async def test_directory_picker_lists_children_and_creates_folders(
    tmp_path, fake_engine_factory
):
    fake_engine_factory()
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(tmp_path / "workspace"))
    registry = _registry(runtime)

    listing = await registry.unary_handler("directoryPicker/list")({"path": str(tmp_path)})
    assert listing["path"] == str(tmp_path)
    assert [entry["name"] for entry in listing["entries"]] == ["alpha", "beta"]
    assert listing["crumbs"][-1]["path"] == str(tmp_path)
    assert listing["home"]

    made = await registry.unary_handler("directoryPicker/createDirectory")(
        {"path": str(tmp_path), "name": "gamma"}
    )
    assert Path(made).is_dir()
    with pytest.raises(RemoteError) as excinfo:
        await registry.unary_handler("directoryPicker/list")({"path": str(tmp_path / "file.txt")})
    assert excinfo.value.code == "workspace/invalid-path"


def test_replay_filter_drops_frames_the_snapshot_already_carries():
    """The follow handoff must not re-send what the opening snapshot covered."""
    from javis.app.web.session_runtime import replay_should_skip

    covered = {"type": "event", "event": {"type": "turn/start", "seq": 7}}
    assert replay_should_skip(covered, cursor=7, revision=None, next_index=0) is True
    later = {"type": "event", "event": {"type": "turn/end", "seq": 8}}
    assert replay_should_skip(later, cursor=7, revision=None, next_index=0) is False

    baseline_chunk = {
        "type": "assistant-stream",
        "frame": {"type": "chunk", "revision": 3, "index": 1, "chunk": {}},
    }
    assert replay_should_skip(baseline_chunk, cursor=7, revision=3, next_index=2) is True
    fresh_chunk = {
        "type": "assistant-stream",
        "frame": {"type": "chunk", "revision": 3, "index": 2, "chunk": {}},
    }
    assert replay_should_skip(fresh_chunk, cursor=7, revision=3, next_index=2) is False
    other_revision = {
        "type": "assistant-stream",
        "frame": {"type": "chunk", "revision": 4, "index": 0, "chunk": {}},
    }
    assert replay_should_skip(other_revision, cursor=7, revision=3, next_index=2) is False
    assert replay_should_skip(None, cursor=7, revision=3, next_index=2) is False


async def test_history_pages_end_exactly_at_the_requested_cursor(
    web_runtime: SessionRuntime,
):
    """A page whose last record is not the requested cursor is a journal gap."""
    first = await _run_turn(web_runtime, "turn one")
    await web_runtime.prompt(first, "turn two")
    assert first.task is not None
    await first.task
    registry = _registry(web_runtime)
    handler = registry.unary_handler("session/page")
    total = first.seq
    assert total >= 11

    tail = await handler({"request": {"sessionId": first.session_id, "throughSeq": total}})
    assert tail["records"][-1]["event"]["seq"] == total
    assert tail["hasMore"] is False

    older = await handler({"request": {"sessionId": first.session_id, "beforeSeq": 5}})
    assert older["records"][-1]["event"]["seq"] == 4

    # `throughSeq` is the authority when the client supplies both.
    middle = total - 4
    through = await handler(
        {"request": {"sessionId": first.session_id, "beforeSeq": middle, "throughSeq": middle}}
    )
    assert through["records"][-1]["event"]["seq"] == middle

    empty = await handler({"request": {"sessionId": first.session_id, "beforeSeq": 0}})
    assert empty == {"records": [], "hasMore": False}

    capped = await handler(
        {"request": {"sessionId": first.session_id, "throughSeq": total, "maxMessages": 3}}
    )
    assert len(capped["records"]) == 3
    assert capped["records"][-1]["event"]["seq"] == total
    assert capped["hasMore"] is True
