"""Entry-layer tests for javis.app.app mode dispatch."""

from __future__ import annotations

import sys

import pytest

from javis.app.app import run_tui_mode
from javis.app.tui.react_launcher import _build_backend_command, _get_frontend_dir


def test_tui_frontend_dir_resolves_the_repo_layout() -> None:
    frontend = _get_frontend_dir()
    assert (frontend / "package.json").is_file()


def test_tui_backend_command_uses_tui_subcommand() -> None:
    command = _build_backend_command(
        cwd="/tmp/proj",
        workspace="/tmp/ws",
        model="m1",
        max_turns=4,
        plugins="/tmp/cordis.yml",
    )

    assert command == [
        sys.executable,
        "-m",
        "javis",
        "tui",
        "--backend-only",
        "--cwd",
        "/tmp/proj",
        "--workspace",
        "/tmp/ws",
        "--model",
        "m1",
        "--max-turns",
        "4",
        "--plugins",
        "/tmp/cordis.yml",
    ]


@pytest.mark.asyncio
async def test_run_tui_mode_backend_only_dispatches_to_backend(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_backend(**kwargs: object) -> int:
        captured.update(kwargs)
        return 7

    monkeypatch.setattr("javis.app.app.run_tui_backend", fake_backend)
    code = await run_tui_mode(
        backend_only=True,
        cwd="/tmp/proj",
        model="m1",
        max_turns=4,
        workspace="/tmp/ws",
    )
    assert code == 7
    assert captured["cwd"] == "/tmp/proj"
    assert captured["model"] == "m1"
    assert captured["max_turns"] == 4
    assert captured["workspace"] == "/tmp/ws"


@pytest.mark.asyncio
async def test_run_tui_mode_default_dispatches_to_react_launcher(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_launcher(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("javis.app.app.launch_react_tui", fake_launcher)
    code = await run_tui_mode(cwd="/tmp/proj")
    assert code == 0
    assert captured["cwd"] == "/tmp/proj"
