"""Import and wiring tests for the default xli CLI."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any, assert_type

import pytest

import javis.app.cli.cli as cli_module
from javis.app.cli import xli
from javis.app.cli.context import Context


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
