"""Import and wiring tests for the default xli CLI."""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any, Self, assert_type

import pytest

import javis.app.cli.cli as cli_module
from javis.agent.agent.agent import BaseReactAgent
from javis.agent.agent.protocol import (
    SessionEvent,
    TextDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from javis.agent.model.model import Model
from javis.agent.model.openai.openai_chat import OpenAIChat
from javis.agent.model.request import ModelRequest
from javis.agent.model.response import ModelResponse
from javis.agent.tools.base import ToolRegister
from javis.app.cli import xli
from javis.app.cli.context import Context


class _TestModel(Model):
    def __init__(self, rounds: list[list[Any]]) -> None:
        super().__init__(model="test-stream-model")
        self.rounds = rounds

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        raise NotImplementedError

    async def ainvoke(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def ainvoke_stream(self, request: ModelRequest) -> AsyncIterator[Any]:
        for chunk in self.rounds.pop(0):
            yield chunk


class _RecordingStream(AbstractContextManager["_RecordingStream"]):
    def __init__(self, writes: list[str]) -> None:
        self.writes = writes

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def write(self, chunk: str) -> None:
        self.writes.append(chunk)


class _RecordingToolCard:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update(self, **fields: Any) -> Self:
        self.updates.append(fields)
        return self


class _RecordingUI:
    def __init__(self) -> None:
        self.stream_count = 0
        self.writes: list[str] = []
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []
        self.cards: list[_RecordingToolCard] = []

    def streaming(self, role: str) -> _RecordingStream:
        assert role == "assistant"
        self.stream_count += 1
        return _RecordingStream(self.writes)

    def tool(self, name: str, **kwargs: Any) -> _RecordingToolCard:
        self.tool_calls.append((name, kwargs))
        card = _RecordingToolCard()
        self.cards.append(card)
        return card


async def _events(*events: SessionEvent) -> AsyncIterator[SessionEvent]:
    for event in events:
        yield event


def test_cli_imports_the_vendored_xli_package() -> None:
    xli = importlib.import_module("javis.app.cli.xli")

    assert xli.__version__ == "0.2.0"
    assert isinstance(cli_module._create_ui(), xli.UI)


def test_ui_requires_a_non_optional_context() -> None:
    with pytest.raises(TypeError, match="context"):
        xli.UI()  # type: ignore[call-arg]


def test_cli_context_has_its_concrete_non_optional_type() -> None:
    ui = cli_module._create_ui()

    assert_type(ui.context, Context)


def test_cli_has_no_module_level_ui_global() -> None:
    assert not hasattr(cli_module, "ui")


def test_create_ui_registers_the_tools_command() -> None:
    ui = cli_module._create_ui()

    command, args = ui._slash.parse("/tools")

    assert command is not None
    assert command.name == "tools"
    assert args == ""


def test_cli_demo_uses_the_real_streaming_agent() -> None:
    demo = importlib.import_module("examples.cli_demo.cli")

    assert isinstance(demo.ui.context.agent, BaseReactAgent)
    assert isinstance(demo.ui.context.agent.model, OpenAIChat)


@pytest.mark.asyncio
async def test_cli_demo_routes_prompts_through_the_agent(monkeypatch) -> None:
    demo = importlib.import_module("examples.cli_demo.cli")
    rendered: list[SessionEvent] = []

    async def fake_run(prompt: str) -> AsyncIterator[SessionEvent]:
        assert prompt == "hello"
        yield TextDeltaEvent(data={"text": "hi"})

    async def fake_render(
        _ui: object,
        _ctx: object,
        events: AsyncIterator[SessionEvent],
    ) -> None:
        rendered.extend([event async for event in events])

    monkeypatch.setattr(demo.ui.context.agent, "run", fake_run)
    monkeypatch.setattr(demo, "_render_turn", fake_render)

    await demo.reply("hello")

    assert rendered == [TextDeltaEvent(data={"text": "hi"})]


def test_create_ui_injects_a_real_model_and_tool_registry() -> None:
    model = _TestModel([])
    tools = ToolRegister()

    ui = cli_module._create_ui(model=model, tools=tools)

    assert ui.context.agent.model is model
    assert ui.context.agent.tools is tools


def test_build_model_reads_openai_compatible_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    model = cli_module._build_model()

    assert isinstance(model, OpenAIChat)
    assert model.api_key == "test-key"
    assert model.base_url == "http://localhost:8000/v1"
    assert model.model == "test-model"


@pytest.mark.asyncio
async def test_render_turn_writes_real_model_deltas_to_one_stream() -> None:
    ui = _RecordingUI()
    ctx = SimpleNamespace(agent=SimpleNamespace(tokens=0))

    await cli_module._render_turn(
        ui,  # type: ignore[arg-type]
        ctx,  # type: ignore[arg-type]
        _events(
            TextDeltaEvent(data={"text": "hel"}),
            TextDeltaEvent(data={"text": "lo"}),
        ),
    )

    assert ui.stream_count == 1
    assert ui.writes == ["hel", "lo"]


@pytest.mark.asyncio
async def test_render_turn_updates_tool_card_by_call_id() -> None:
    ui = _RecordingUI()
    ctx = SimpleNamespace(agent=SimpleNamespace(tokens=0))

    await cli_module._render_turn(
        ui,  # type: ignore[arg-type]
        ctx,  # type: ignore[arg-type]
        _events(
            ToolCallStartEvent(
                data={"id": "call_1", "name": "echo", "arguments": {"text": "hi"}}
            ),
            ToolCallEndEvent(
                data={
                    "id": "call_1",
                    "name": "echo",
                    "output": "hi",
                    "is_error": False,
                }
            ),
        ),
    )

    assert ui.tool_calls == [
        ("echo", {"status": "running", "args": {"text": "hi"}})
    ]
    assert ui.cards[0].updates == [{"status": "done", "output": "hi"}]


def test_launch_cli_creates_a_context_and_runs_the_ui(monkeypatch) -> None:
    events: list[str] = []
    prompt_handler: Any = None

    def on_prompt(handler: Any) -> None:
        nonlocal prompt_handler
        prompt_handler = handler

    ui = SimpleNamespace(
        context=SimpleNamespace(
            session_id="test",
            agent=SimpleNamespace(model="test", persona="default", turn=0, tokens=0),
        ),
        status=SimpleNamespace(set=lambda **_: events.append("status")),
        on_prompt=on_prompt,
        on_interrupt=lambda handler: events.append("interrupt"),
        run=lambda: events.append("run"),
    )
    monkeypatch.setattr(cli_module, "_create_ui", lambda: ui)

    cli_module.launch_cli()

    assert ui.context.agent is not None
    assert prompt_handler is not None
    assert events == ["interrupt", "status", "run"]


def test_launch_cli_propagates_run_errors_and_says_bye(monkeypatch, capsys) -> None:
    def fail() -> None:
        raise RuntimeError("boom")

    ui = SimpleNamespace(
        context=SimpleNamespace(
            session_id="test",
            agent=SimpleNamespace(model="test", persona="default", turn=0, tokens=0),
        ),
        status=SimpleNamespace(set=lambda **_: None),
        on_prompt=lambda handler: None,
        on_interrupt=lambda handler: None,
        run=fail,
    )
    monkeypatch.setattr(cli_module, "_create_ui", lambda: ui)

    with pytest.raises(RuntimeError, match="boom"):
        cli_module.launch_cli()

    assert capsys.readouterr().out == "Bye\n"
