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

from javis.app.app import run_print_mode, run_tui_mode
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
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the javis workspace (defaults to ~/.javis)"),
    max_turns: int | None = typer.Option(None, "--max-turns", help="Override max turns"),
    plugins: str | None = typer.Option(None, "--plugins", help="Plugin composition file (cordis.yml); defaults to <workspace>/cordis.yml"),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Working directory"),
    backend_only: bool = typer.Option(False, "--backend-only", hidden=True),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Launch javis or run a subcommand."""
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    else:
        # Quiet by default: the TUI inherits stderr, so library noise (httpx
        # prints an INFO line per HTTP request) would smear across the screen.
        logging.basicConfig(
            level=logging.WARNING,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        # Keep javis' own INFO visible (config creation, lifecycle, …).
        # Setting the parent logger covers all javis.* sub-loggers via
        # effective-level inheritance.
        logging.getLogger("javis").setLevel(logging.INFO)

    if ctx.invoked_subcommand is not None:
        return

    cwd_path = str(Path(cwd).resolve())
    workspace_root = initialize_workspace(workspace)

    if backend_only:
        raise SystemExit(
            asyncio.run(
                run_tui_mode(
                    backend_only=True,
                    cwd=cwd_path,
                    workspace=workspace_root,
                    model=model,
                    max_turns=max_turns,
                    plugins=plugins,
                )
            )
        )

    if print_mode is not None:
        raise SystemExit(
            asyncio.run(
                run_print_mode(
                    prompt=print_mode,
                    cwd=cwd_path,
                    workspace=workspace_root,
                    model=model,
                    max_turns=max_turns,
                    plugins=plugins,
                )
            )
        )

    raise SystemExit(
        asyncio.run(
            run_tui_mode(
                cwd=cwd_path,
                workspace=workspace_root,
                model=model,
                max_turns=max_turns,
                plugins=plugins,
            )
        )
    )

@app.command("doctor")
def doctor(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the javis workspace (defaults to ~/.javis)"),
) -> None:
    """Check the javis workspace and frontend layout."""
    from javis.app.react_launcher import _get_frontend_dir
    from javis.session.workspace import workspace_health

    workspace_root = initialize_workspace(workspace)
    print(f"javis workspace: {workspace_root}")
    for key, ok in workspace_health(workspace_root).items():
        print(f"  {key}: {'ok' if ok else 'missing'}")

    print(f"frontend dir:   {_get_frontend_dir()}")
    print(f"cwd:            {Path(cwd).resolve()}")


@app.command("version")
def version_cmd() -> None:
    """Show the javis version."""
    from javis import __version__

    print(f"javis {__version__}")


__all__ = ["app"]
