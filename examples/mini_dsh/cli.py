"""Standalone driver for the plugin-harness example.

Runs the harness through javis' normal plugin pipeline — ``build_runtime``
mounts ``cordis.yml``, the harness plugin provides the engine, and this file
drives it with ``handle_line`` (the same dispatch core the TUI backend uses).
No React frontend required.

Run from anywhere (javis must be importable):

    uv run python examples/plugin_harness/cli.py --demo
    uv run python examples/plugin_harness/cli.py --prompt "what is 2+2"
    uv run python examples/plugin_harness/cli.py            # interactive REPL

Or use the full TUI:

    uv run javis --plugins examples/plugin_harness/cordis.yml
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from javis.app.runtime import RuntimeBundle, build_runtime, handle_line
from javis.contracts.messages import ConversationMessage
from javis.contracts.types import (
    AgentError,
    AgentEvent,
    AgentReasoningDelta,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)

_HERE = Path(__file__).resolve().parent
_COMPOSITION = _HERE / "cordis.yml"


def _summarize(tool_input: dict[str, object]) -> str:
    return ", ".join(f"{key}={str(value)[:40]}" for key, value in tool_input.items())


async def _render_event(event: AgentEvent) -> None:
    """Print the AgentEvent stream in a terminal-friendly shape."""
    if isinstance(event, AgentTextDelta):
        sys.stdout.write(event.text)
        sys.stdout.flush()
    elif isinstance(event, AgentReasoningDelta):
        sys.stderr.write(f"\n[reasoning] {event.text}\n")
        sys.stderr.flush()
    elif isinstance(event, AgentToolCallStart):
        sys.stderr.write(f"\n⚙ {event.tool_name}({_summarize(event.tool_input)})\n")
        sys.stderr.flush()
    elif isinstance(event, AgentToolCallResult):
        status = "ok" if not event.is_error else "error"
        sys.stderr.write(f"  → [{status}] {event.output[:200]}\n")
        sys.stderr.flush()
    elif isinstance(event, AgentTurnEnd):
        sys.stdout.write("\n")
        sys.stdout.flush()
    elif isinstance(event, AgentStatus):
        sys.stderr.write(f"{event.message}\n")
        sys.stderr.flush()
    elif isinstance(event, AgentError):
        sys.stderr.write(f"[error] {event.message}\n")
        sys.stderr.flush()


async def _run(args: argparse.Namespace) -> int:
    workspace = args.workspace
    if args.demo and workspace is None:
        workspace = Path(tempfile.mkdtemp(prefix="plugin-harness-demo-"))
        print(f"[javis] offline demo workspace: {workspace}", file=sys.stderr)
    if args.demo:
        os.environ["HARNESS_PROVIDER"] = "scripted"

    bundle: RuntimeBundle | None = None
    try:
        bundle = await build_runtime(
            cwd=str(Path.cwd()),
            workspace=workspace,
            plugins=str(_COMPOSITION),
        )
        print(
            f"[javis] engine: {type(bundle.engine).__name__} "
            f"(composition: {_COMPOSITION.name})",
            file=sys.stderr,
        )

        async def _print_system(message: str) -> None:
            print(message, file=sys.stderr)

        async def _clear_output() -> None:
            return None

        saw_error = False

        async def _render_checked(event: AgentEvent) -> None:
            nonlocal saw_error
            if isinstance(event, AgentError):
                saw_error = True
            await _render_event(event)

        if args.prompt or args.demo:
            prompt = args.prompt or "Save a note to the workspace and read it back"
            # plain prompt: never dispatch slash commands
            await handle_line(
                bundle,
                prompt,
                print_system=_print_system,
                render_event=_render_checked,
                clear_output=_clear_output,
                user_message=ConversationMessage.from_user_text(prompt),
            )
            return 1 if saw_error else 0

        # interactive REPL (slash commands work: /help, /harness, /exit ...)
        while True:
            try:
                line = input("javis> ").strip()
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
                break
            if not line:
                continue
            keep = await handle_line(
                bundle,
                line,
                print_system=_print_system,
                render_event=_render_checked,
                clear_output=_clear_output,
            )
            if not keep:
                break
        return 0
    finally:
        if bundle is not None:
            await bundle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", help="Run one prompt and exit")
    parser.add_argument("--demo", action="store_true", help="Force the offline scripted provider (no API key)")
    parser.add_argument("--workspace", help="javis workspace (default: ~/.javis; --demo uses a temp dir)")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
