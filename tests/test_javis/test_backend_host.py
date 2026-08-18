"""End-to-end tests for _JavisBackendHost — drives the FakeBackend test double
through the full backend host pipeline (request dispatch, emit, modal
futures).
"""

from __future__ import annotations

import pytest

from javis.host.backend_host import _BackendHostConfig, _JavisBackendHost
from tests.test_javis.fake_backend import FakeBackend
from javis.host.wire import BackendEvent
from javis.host.runtime import build_javis_runtime, close_runtime, start_runtime


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JAVIS_WORKSPACE", str(tmp_path / "javis-workspace"))
    # A local socks proxy in the environment breaks httpx client construction
    # (socksio not installed); drop proxy vars so OpenAI clients can build.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


async def _make_host(
    isolated_env, model: str = "test-model"
) -> tuple[_JavisBackendHost, list]:
    bundle = await build_javis_runtime(
        cwd=str(isolated_env),
        agent_backend=FakeBackend(),
        model=model,
    )
    host = _JavisBackendHost(
        bundle=bundle,
        config=_BackendHostConfig(cwd=str(isolated_env)),
    )
    events: list = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    return host, events


@pytest.mark.asyncio
async def test_backend_host_processes_turn(isolated_env):
    host, events = await _make_host(isolated_env)
    try:
        should_continue = await host._process_line("hello")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    assert any(event.type == "assistant_delta" for event in events)
    assert any(event.type == "assistant_complete" for event in events)
    assert any(event.type == "line_complete" for event in events)
    # user transcript item
    assert any(
        event.type == "transcript_item"
        and event.item
        and event.item.role == "user"
        and "hello" in event.item.text
        for event in events
    )


@pytest.mark.asyncio
async def test_backend_host_processes_tool_call(isolated_env):
    host, events = await _make_host(isolated_env)
    try:
        await host._process_line("use a tool please")
    finally:
        await close_runtime(host._bundle)

    assert any(event.type == "tool_started" and event.tool_name == "echo" for event in events)
    assert any(event.type == "tool_completed" and event.tool_name == "echo" for event in events)


@pytest.mark.asyncio
async def test_backend_host_processes_error_event(isolated_env):
    host, events = await _make_host(isolated_env)
    try:
        await host._process_line("trigger an error")
    finally:
        await close_runtime(host._bundle)

    assert any(event.type == "error" for event in events)


@pytest.mark.asyncio
async def test_backend_host_processes_slash_command(isolated_env):
    host, events = await _make_host(isolated_env)
    try:
        should_continue = await host._process_line("/version")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    # /version emits a system transcript item
    assert any(
        event.type == "transcript_item"
        and event.item
        and event.item.role == "system"
        for event in events
    )


@pytest.mark.asyncio
async def test_backend_host_emits_ready_state_snapshot(isolated_env):
    """Verify the ready event includes the model name."""
    host, events = await _make_host(isolated_env)
    try:
        await host._emit(
            BackendEvent.ready(
                host._bundle.app_state.get(),
                [f"/{cmd.name}" for cmd in host._bundle.commands.list_commands()],
            )
        )
    finally:
        await close_runtime(host._bundle)

    ready = next(e for e in events if e.type == "ready")
    assert ready.state is not None
    assert ready.state["model"] == "test-model"
    assert ready.state["provider"] == "javis"


@pytest.mark.asyncio
async def test_backend_host_status_snapshot_includes_engine_max_turns(isolated_env):
    """The /turns selector reads bundle.engine.max_turns — verify it doesn't crash."""
    host, _ = await _make_host(isolated_env)
    try:
        # _handle_select_command("turns") reads self._bundle.engine.max_turns
        await host._handle_select_command("turns")
    finally:
        await close_runtime(host._bundle)


@pytest.mark.asyncio
async def test_backend_host_select_command_model(isolated_env):
    """The /model selector should emit a select_request."""
    host, events = await _make_host(isolated_env)
    try:
        await host._handle_select_command("model")
    finally:
        await close_runtime(host._bundle)

    assert any(event.type == "select_request" for event in events)


@pytest.mark.asyncio
async def test_apply_select_theme_updates_state(isolated_env):
    """P0: /theme selector must update AppState, not fall through to the LLM."""
    host, events = await _make_host(isolated_env)
    try:
        await host._apply_select_command("theme", "dark")
    finally:
        await close_runtime(host._bundle)

    assert host._bundle.app_state.get().theme == "dark"


@pytest.mark.asyncio
async def test_apply_select_turns_updates_engine(isolated_env):
    """P0: /turns selector must update QueryEngine.max_turns, not fall through to the LLM."""
    host, events = await _make_host(isolated_env)
    try:
        await host._apply_select_command("turns", "64")
    finally:
        await close_runtime(host._bundle)

    assert host._bundle.engine.max_turns == 64


@pytest.mark.asyncio
async def test_apply_select_turns_unlimited_clears_limit(isolated_env):
    """/turns unlimited must reset the engine's max-turn limit to None."""
    host, events = await _make_host(isolated_env)
    try:
        await host._apply_select_command("turns", "64")
        await host._apply_select_command("turns", "unlimited")
    finally:
        await close_runtime(host._bundle)

    assert host._bundle.engine.max_turns is None
