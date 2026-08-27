"""End-to-end tests for BackendHost — drives the FakeEngine test double
through the full backend host pipeline (request dispatch, emit, modal
futures).
"""

from __future__ import annotations

import pytest

from javis.host.backend_host import BackendHost
from tests.test_javis.fake_backend import FakeEngine
from javis.host.wire import BackendEvent
from javis.host.runtime import build_runtime


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


@pytest.fixture
async def _make_host(
    isolated_env, fake_engine_factory, model: str = "test-model"
) -> tuple[BackendHost, list]:
    fake_engine_factory()
    bundle = await build_runtime(
        cwd=str(isolated_env),
        model=model,
    )
    host = BackendHost(bundle=bundle)
    events: list = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    return host, events


@pytest.mark.asyncio
async def test_backend_host_processes_turn(isolated_env, _make_host):
    host, events = _make_host
    should_continue = await host._process_line("hello")

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
async def test_backend_host_processes_tool_call(isolated_env, _make_host):
    host, events = _make_host
    await host._process_line("use a tool please")

    assert any(event.type == "tool_started" and event.tool_name == "echo" for event in events)
    assert any(event.type == "tool_completed" and event.tool_name == "echo" for event in events)


@pytest.mark.asyncio
async def test_backend_host_processes_error_event(isolated_env, _make_host):
    host, events = _make_host
    await host._process_line("trigger an error")

    assert any(event.type == "error" for event in events)


@pytest.mark.asyncio
async def test_backend_host_processes_slash_command(isolated_env, _make_host):
    host, events = _make_host
    should_continue = await host._process_line("/version")

    assert should_continue is True
    # /version emits a system transcript item
    assert any(
        event.type == "transcript_item"
        and event.item
        and event.item.role == "system"
        for event in events
    )


@pytest.mark.asyncio
async def test_backend_host_emits_ready_state_snapshot(isolated_env, _make_host):
    """Verify the ready event includes the model name."""
    host, events = _make_host
    await host._emit(
        BackendEvent.ready(
            host._bundle.app_state.get(),
            [f"/{cmd.name}" for cmd in host._bundle.commands.list_commands()],
        )
    )

    ready = next(e for e in events if e.type == "ready")
    assert ready.state is not None
    assert ready.state["model"] == "test-model"
    assert ready.state["provider"] == "javis"


@pytest.mark.asyncio
async def test_backend_host_status_snapshot_includes_engine_max_turns(isolated_env, _make_host):
    """The /turns selector reads bundle.engine.max_turns — verify it doesn't crash."""
    host, _ = _make_host
    # _handle_select_command("turns") reads self._bundle.engine.max_turns
    await host._handle_select_command("turns")


@pytest.mark.asyncio
async def test_backend_host_select_command_model(isolated_env, _make_host):
    """The /model selector should emit a select_request."""
    host, events = _make_host
    await host._handle_select_command("model")

    assert any(event.type == "select_request" for event in events)


@pytest.mark.asyncio
async def test_apply_select_theme_updates_state(isolated_env, _make_host):
    """P0: /theme selector must update AppState, not fall through to the LLM."""
    host, events = _make_host
    await host._apply_select_command("theme", "dark")

    assert host._bundle.app_state.get().theme == "dark"


@pytest.mark.asyncio
async def test_apply_select_turns_updates_engine(isolated_env, _make_host):
    """P0: /turns selector must update engine.max_turns, not fall through to the LLM."""
    host, events = _make_host
    await host._apply_select_command("turns", "64")

    assert host._bundle.engine.max_turns == 64


@pytest.mark.asyncio
async def test_apply_select_turns_unlimited_clears_limit(isolated_env, _make_host):
    """/turns unlimited must reset the engine's max-turn limit to None."""
    host, events = _make_host
    await host._apply_select_command("turns", "64")
    await host._apply_select_command("turns", "unlimited")

    assert host._bundle.engine.max_turns is None


@pytest.mark.asyncio
async def test_apply_select_theme_requires_value(isolated_env, _make_host):
    """An empty /theme value leaves the theme unchanged and emits a usage message."""
    host, _ = _make_host
    await host._process_line("/theme")

    assert host._bundle.app_state.get().theme == "default"


@pytest.mark.asyncio
async def test_apply_select_turns_rejects_non_numeric(isolated_env, _make_host):
    """A non-numeric /turns value must leave the existing limit unchanged."""
    host, _ = _make_host
    await host._apply_select_command("turns", "64")
    await host._apply_select_command("turns", "abc")

    assert host._bundle.engine.max_turns == 64


@pytest.mark.asyncio
async def test_apply_select_permissions_updates_state(isolated_env, _make_host):
    """P0: /permissions selector must update the permission mode, not fall through to the LLM."""
    host, _ = _make_host
    await host._apply_select_command("permissions", "plan")

    assert host._bundle.app_state.get().permission_mode == "plan"
    assert host._bundle.engine.tool_metadata["permission_mode"] == "plan"


@pytest.mark.asyncio
async def test_apply_select_permissions_rejects_unknown_mode(isolated_env, _make_host):
    """An unknown /permissions value must leave the existing mode unchanged."""
    host, _ = _make_host
    await host._apply_select_command("permissions", "default")
    await host._apply_select_command("permissions", "turbo")

    assert host._bundle.app_state.get().permission_mode == "default"
    assert host._bundle.engine.tool_metadata["permission_mode"] == "default"
