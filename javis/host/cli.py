"""CLI entry point for javis.

Three modes:
    - default:           launch the React terminal frontend
    - ``--backend-only``: run the JSON-lines backend host on stdin/stdout
    - ``--print``/``-p``: run a single prompt and print to stdout
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer

from javis.host.runtime import run_javis_print_mode
from javis.session.workspace import initialize_workspace

app = typer.Typer(
    name="javis",
    help="javis: a minimal TUI for driving a custom agent.",
    invoke_without_command=True,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    print_mode: str | None = typer.Option(None, "--print", "-p", help="Run a single prompt and exit"),
    model: str | None = typer.Option(None, "--model", help="Model override for this session"),
    engine: str | None = typer.Option(None, "--engine", help="Agent engine (default: config.json, JAVIS_ENGINE, or corecoder)"),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the javis workspace (defaults to ~/.javis)"),
    max_turns: int | None = typer.Option(None, "--max-turns", help="Override max turns"),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Working directory"),
    backend_only: bool = typer.Option(False, "--backend-only", hidden=True),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Launch javis or run a subcommand."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if ctx.invoked_subcommand is not None:
        return

    cwd_path = str(Path(cwd).resolve())
    workspace_root = initialize_workspace(workspace)

    if backend_only:
        from javis.host.backend_host import run_javis_backend

        raise SystemExit(
            asyncio.run(
                run_javis_backend(
                    cwd=cwd_path,
                    workspace=workspace_root,
                    model=model,
                    max_turns=max_turns,
                    engine=engine,
                )
            )
        )

    if print_mode is not None:
        raise SystemExit(
            asyncio.run(
                run_javis_print_mode(
                    prompt=print_mode,
                    cwd=cwd_path,
                    workspace=workspace_root,
                    model=model,
                    max_turns=max_turns,
                    engine=engine,
                )
            )
        )

    from javis.host.react_launcher import launch_react_tui

    raise SystemExit(
        asyncio.run(
            launch_react_tui(
                cwd=cwd_path,
                workspace=workspace_root,
                model=model,
                max_turns=max_turns,
                engine=engine,
            )
        )
    )

@app.command("version")
def version_cmd() -> None:
    """Show the javis version."""
    from javis import __version__

    print(f"javis {__version__}")


__all__ = ["app"]
