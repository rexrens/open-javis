"""Entry-layer tests for javis.host.app mode dispatch."""

from __future__ import annotations

import pytest

from javis.host.app import run_tui_mode


@pytest.mark.asyncio
async def test_run_tui_mode_backend_only_dispatches_to_backend(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_backend(**kwargs: object) -> int:
        captured.update(kwargs)
        return 7

    monkeypatch.setattr("javis.host.app.run_backend_mode", fake_backend)
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

    monkeypatch.setattr("javis.host.app.launch_react_tui", fake_launcher)
    code = await run_tui_mode(cwd="/tmp/proj")
    assert code == 0
    assert captured["cwd"] == "/tmp/proj"
