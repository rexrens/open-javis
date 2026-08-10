"""CLI entry point for javis."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer

from javis.runtime import launch_javis_tui, run_javis_backend, run_javis_print_mode

app = typer.Typer(
    name="javis",
    help="javis: a custom agent app built on the OpenHarness TUI.",
    invoke_without_command=True,
    add_completion=False,
)


@app.callback()
def main(
    ctx: typer.Context,
    cwd: str = typer.Option(None, "--cwd", help="Working directory for the agent."),
    workspace: str = typer.Option(None, "--workspace", help="Path to the javis workspace (~/.javis)."),
    model: str = typer.Option(None, "--model", help="Model name shown in the UI status bar."),
    max_turns: int = typer.Option(None, "--max-turns", help="Maximum agent turns per prompt."),
    backend_only: bool = typer.Option(False, "--backend-only", help="Run the structured backend host (used internally by the React frontend)."),
    print_prompt: str = typer.Option(None, "--print", "-p", help="Run a single prompt in print mode and exit."),
) -> None:
    """Run javis — launch the TUI by default, or a single prompt with -p."""
    if ctx.invoked_subcommand is not None:
        return

    if backend_only:
        rc = asyncio.run(
            run_javis_backend(cwd=cwd, workspace=workspace, model=model, max_turns=max_turns)
        )
        raise typer.Exit(code=rc)

    if print_prompt is not None:
        rc = asyncio.run(
            run_javis_print_mode(
                prompt=print_prompt,
                cwd=cwd,
                workspace=workspace,
                model=model,
                max_turns=max_turns,
            )
        )
        raise typer.Exit(code=rc)

    rc = asyncio.run(
        launch_javis_tui(cwd=cwd, workspace=workspace, model=model, max_turns=max_turns)
    )
    raise typer.Exit(code=rc)


@app.command()
def version() -> None:
    """Print the javis version and exit."""
    from javis import __version__

    typer.echo(f"javis {__version__}")


if __name__ == "__main__":
    app()
