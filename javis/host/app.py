"""Application entry points: mode dispatch for javis.

Forked from openharness.ui.app and trimmed: javis exposes exactly two
user-facing modes — ``run_print_mode`` (single prompt, print to stdout) and
``run_tui_mode`` (React terminal frontend, which spawns the JSON-lines backend
itself via ``OPENHARNESS_FRONTEND_CONFIG.backend_command``). The backend host
is an implementation detail of TUI mode: ``backend_only=True`` runs it
directly, mirroring openharness' ``run_repl(backend_only=...)``.

Layer layout (entry → implementation):
    javis.cli           typer parsing only
    javis.host.app      this file — entry functions
    javis.host.runtime  build_runtime / handle_line
    javis.host.backend_host / react_launcher  implementations
"""

from __future__ import annotations

from pathlib import Path

from javis.host.backend_host import run_backend_mode
from javis.host.react_launcher import launch_react_tui


async def run_tui_mode(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    backend_only: bool = False,
) -> int:
    """Run the interactive React TUI, or the JSON-lines backend it spawns.

    ``backend_only=True`` is the mode the React frontend launches via
    ``python -m javis --backend-only`` — it is not a third user-facing mode.
    """
    if backend_only:
        return await run_backend_mode(
            cwd=cwd,
            workspace=workspace,
            model=model,
            max_turns=max_turns,
        )
    return await launch_react_tui(
        cwd=cwd,
        workspace=workspace,
        model=model,
        max_turns=max_turns,
    )


__all__ = ["run_tui_mode"]
