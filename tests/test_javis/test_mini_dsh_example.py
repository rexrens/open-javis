"""End-to-end test for the plugin-harness example.

Mounts ``examples/mini_dsh/cordis.yml`` through ``build_runtime`` with
the offline scripted provider and asserts the full loop: plugin tools reach
the engine, the ``/harness`` command is registered, events stream, and
``close()`` disposes plugins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from javis.app.runtime import build_runtime
from javis.contracts.types import (
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)

_COMPOSITION = Path(__file__).resolve().parents[2] / "examples" / "mini_dsh" / "cordis.yml"


@pytest.fixture
def example_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated javis workspace + forced scripted provider."""
    ws = tmp_path / "javis-workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("JAVIS_WORKSPACE", str(ws))
    monkeypatch.setenv("HARNESS_PROVIDER", "scripted")
    monkeypatch.chdir(tmp_path)
    for var in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(var, raising=False)
    return ws


@pytest.mark.skip(
    reason="旧 plugin-harness 栈随 Task 10 providers.py 重写失效（providers.ChatProvider 移除）；"
    "Task 16 将此测试重写为新 cli 的 E2E。"
)
@pytest.mark.asyncio
async def test_plugin_harness_example_runs_tool_loop(example_workspace: Path) -> None:
    """The example composition yields a HarnessEngine that sees plugin tools,
    runs a full tool-call round, and registers its /harness command."""
    bundle = await build_runtime(
        cwd=str(example_workspace.parent),
        plugins=str(_COMPOSITION),
    )

    assert type(bundle.engine).__name__ == "HarnessEngine"
    assert "workspace_note" in getattr(bundle.engine, "tools", [])
    assert {cmd.name for cmd in bundle.commands.list_commands()} >= {"harness", "help", "status"}

    events = [
        event
        async for event in bundle.engine.submit_message("Save a note and read it back")
    ]
    tool_starts = [e for e in events if isinstance(e, AgentToolCallStart)]
    tool_results = [e for e in events if isinstance(e, AgentToolCallResult)]
    assert [e.tool_name for e in tool_starts] == ["workspace_note", "read_file"]
    assert not any(e.is_error for e in tool_results)
    assert any(isinstance(e, AgentTurnEnd) for e in events)
    streamed_text = "".join(e.text for e in events if isinstance(e, AgentTextDelta))
    assert "plugin harness demo" in streamed_text

    notes = example_workspace / "notes.txt"
    assert notes.exists()
    assert "Hello from the plugin harness demo" in notes.read_text(encoding="utf-8")

    await bundle.close()
    assert bundle.context is not None
    assert bundle.context.get("engine") is None
