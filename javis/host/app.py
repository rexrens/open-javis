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

import os
import sys
from pathlib import Path

from javis.contracts.messages import ConversationMessage
from javis.contracts.types import (
    AgentError,
    AgentEvent,
    AgentStatus,
    AgentTextDelta,
    AgentTurnEnd,
)
from javis.host.backend_host import run_backend_mode
from javis.host.react_launcher import launch_react_tui
from javis.host.runtime import build_runtime, handle_line


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


async def run_print_mode(
    *,
    prompt: str,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
) -> int:
    """Run a single prompt and print the assistant output to stdout."""
    cwd_path = str(Path(cwd or Path.cwd()).resolve())
    previous_cwd = Path.cwd()
    os.chdir(cwd_path)

    try:
        bundle = await build_runtime(
            cwd=cwd_path,
            model=model,
            max_turns=max_turns,
            workspace=workspace,
        )

        async def _print_system(message: str) -> None:
            print(message, file=sys.stderr)

        saw_error = False

        async def _render_event(event: AgentEvent) -> None:
            nonlocal saw_error
            if isinstance(event, AgentTextDelta):
                sys.stdout.write(event.text)
                sys.stdout.flush()
            elif isinstance(event, AgentTurnEnd):
                sys.stdout.write("\n")
                sys.stdout.flush()
            elif isinstance(event, AgentError):
                saw_error = True
                print(event.message, file=sys.stderr)
            elif isinstance(event, AgentStatus):
                print(event.message, file=sys.stderr)
            # Tool start/result events are not printed in print mode.

        async def _clear_output() -> None:
            return None

        await handle_line(
            bundle,
            prompt,
            print_system=_print_system,
            render_event=_render_event,
            clear_output=_clear_output,
            # Print mode is a plain prompt: never dispatch slash commands.
            user_message=ConversationMessage.from_user_text(prompt),
        )
        return 1 if saw_error else 0
    finally:
        os.chdir(previous_cwd)


__all__ = ["run_print_mode", "run_tui_mode"]
