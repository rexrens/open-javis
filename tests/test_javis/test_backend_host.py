"""End-to-end tests for JavisBackendHost — drives a scripted CoreCoder agent
through the full ReactBackendHost pipeline (request dispatch, emit, modal
futures).

Pattern mirrors tests/test_ui/test_react_backend.py but uses
``build_javis_runtime`` instead of ``build_runtime``.
"""

from __future__ import annotations

import pytest

from javis.backend_host import JavisBackendHost
from javis.corecoder.agent import Agent
from javis.corecoder.llm import LLMResponse, ScriptedLLM, ToolCall
from javis.runtime import build_javis_runtime
from openharness.ui.backend_host import BackendHostConfig
from openharness.ui.runtime import close_runtime, start_runtime


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JAVIS_WORKSPACE", str(tmp_path / "javis-workspace"))
    # A local socks proxy in the environment breaks httpx client construction
    # (socksio not installed); drop proxy vars so OpenAI clients can build.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _scripted_agent(script: list[LLMResponse]) -> Agent:
    return Agent(llm=ScriptedLLM(script=script), max_rounds=10)


async def _make_host(
    isolated_env, script: list[LLMResponse] | None = None, model: str = "test-model"
) -> tuple[JavisBackendHost, list]:
    bundle = await build_javis_runtime(
        cwd=str(isolated_env),
        agent=_scripted_agent(script or []),
        model=model,
    )
    host = JavisBackendHost(
        bundle=bundle,
        config=BackendHostConfig(cwd=str(isolated_env)),
    )
    events: list = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    return host, events


@pytest.mark.asyncio
async def test_backend_host_processes_turn(isolated_env):
    host, events = await _make_host(isolated_env, script=[LLMResponse(content="hello there")])
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
    host, events = await _make_host(isolated_env, script=[
        LLMResponse(tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "echo hi"})]),
        LLMResponse(content="ran the command"),
    ])
    try:
        await host._process_line("use a tool please")
    finally:
        await close_runtime(host._bundle)

    assert any(event.type == "tool_started" and event.tool_name == "bash" for event in events)
    assert any(event.type == "tool_completed" and event.tool_name == "bash" for event in events)


@pytest.mark.asyncio
async def test_backend_host_processes_error_event(isolated_env):
    # Empty script: the first model call raises, which must surface as an error event.
    host, events = await _make_host(isolated_env, script=[])
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
    """Verify the ready event includes the mock model name."""
    host, events = await _make_host(isolated_env)
    # Manually emit ready (as run() would)
    from openharness.tasks import get_task_manager
    from openharness.ui.protocol import BackendEvent

    await host._emit(
        BackendEvent.ready(
            host._bundle.app_state.get(),
            get_task_manager().list_tasks(),
            [f"/{cmd.name}" for cmd in host._bundle.commands.list_commands()],
        )
    )
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
