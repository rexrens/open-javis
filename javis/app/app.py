"""Application entry points: mode dispatch for javis.

Forked from openharness.ui.app and trimmed: javis exposes exactly three
user-facing modes — ``run_print_mode`` (single prompt, print to stdout),
``run_tui_mode`` (React terminal frontend, which spawns the JSON-lines backend
itself via ``OPENHARNESS_FRONTEND_CONFIG.backend_command``) and ``run_web_mode``
(the dsh browser UI served by a javis host). Each backend host is an
implementation detail of its frontend mode: ``backend_only=True`` runs it
directly, mirroring openharness' ``run_repl(backend_only=...)``.

Layer layout (entry → implementation):
    javis.cli           typer parsing only
    javis.app.app      this file — entry functions
    javis.app.runtime  build_runtime / handle_line
    javis.app.backend_host / react_launcher / web_launcher  implementations
    javis.app.web.*    the FastAPI host behind run_web_mode
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from javis.app.backend_host import run_backend_mode
from javis.app.react_launcher import launch_react_tui
from javis.app.runtime import RuntimeBundle, build_runtime, handle_line
from javis.app.web_launcher import launch_web, run_web_backend
from javis.contracts.messages import ConversationMessage
from javis.contracts.types import (
    AgentError,
    AgentEvent,
    AgentStatus,
    AgentTextDelta,
    AgentTurnEnd,
)


async def run_tui_mode(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    plugins: str | Path | None = None,
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
            plugins=plugins,
        )
    return await launch_react_tui(
        cwd=cwd,
        workspace=workspace,
        model=model,
        max_turns=max_turns,
        plugins=plugins,
    )


async def run_print_mode(
    *,
    prompt: str,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    plugins: str | Path | None = None,
) -> int:
    """Run a single prompt and print the assistant output to stdout."""
    cwd_path = str(Path(cwd or Path.cwd()).resolve())
    previous_cwd = Path.cwd()
    os.chdir(cwd_path)

    bundle: RuntimeBundle | None = None
    try:
        bundle = await build_runtime(
            cwd=cwd_path,
            model=model,
            max_turns=max_turns,
            workspace=workspace,
            plugins=plugins,
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
        if bundle is not None:
            await bundle.close()
        os.chdir(previous_cwd)


async def run_web_mode(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    plugins: str | Path | None = None,
    port: int | None = None,
    open_browser: bool = True,
    in_process: bool = False,
    rebuild_assets: bool = False,
    dsh_root: str | Path | None = None,
    backend_only: bool = False,
) -> int:
    """Run the dsh web UI backed by the javis harness.

    ``backend_only=True`` is the mode the launcher spawns via
    ``python -m javis web --backend-only``: it runs the host directly and
    prints this process's authenticated URL. It is not a fourth user-facing
    mode.
    """
    if backend_only:
        return await run_web_backend(
            cwd=cwd,
            workspace=workspace,
            model=model,
            max_turns=max_turns,
            plugins=plugins,
            port=port,
        )
    return await launch_web(
        cwd=cwd,
        workspace=workspace,
        model=model,
        max_turns=max_turns,
        plugins=plugins,
        port=port,
        open_browser=open_browser,
        in_process=in_process,
        rebuild_assets=rebuild_assets,
        dsh_root=dsh_root,
    )


__all__ = ["run_print_mode", "run_tui_mode", "run_web_mode"]
