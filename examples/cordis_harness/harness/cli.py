"""``cdh``: parse argv, boot the composition layers, and wait for it to finish.

The composition is layered: a base (``--config``, else ``./cordis.yml``, else
the packaged default) plus patches — the harness home's ``cordis.patch.yml``,
the working directory's, then every ``--patch`` file. ``--dump-config`` prints
the merged result and exits; ``--no-patches`` ignores the ambient layers.

Exit codes: ``0`` for a clean run, ``1`` when the composition fails to load
or a fiber fails, ``130`` when the user interrupts before the front end owns
the terminal.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from javis.cordis import Context

from . import composition, options
from .boot import collect_fibers, failed_fibers, layer_patches, load_layers, resolve_config_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cdh", description="cordis-harness interactive agent")
    parser.add_argument("--config", help="base composition file (default: ./cordis.yml, then the packaged default)")
    parser.add_argument(
        "--patch",
        action="append",
        default=[],
        metavar="FILE",
        help="patch layer applied over the base; repeatable, applied in order",
    )
    parser.add_argument(
        "--no-patches",
        action="store_true",
        help="ignore the ambient home and project patch layers (explicit --patch files still apply)",
    )
    parser.add_argument("--dump-config", action="store_true", help="print the merged composition and exit")
    parser.add_argument(
        "--home",
        help=f"harness home: user patch, generated composition and sessions (default: {composition.DEFAULT_HOME})",
    )
    parser.add_argument("--resume", metavar="ID|latest", help="continue a stored session")
    parser.add_argument("--model", help="override the model id")
    parser.add_argument("--provider", help="override the provider route")
    parser.add_argument("--session-dir", help="directory holding session logs")
    parser.add_argument("--cwd", help="working directory for tools (default: the current directory)")
    parser.add_argument("--yolo", action="store_true", default=None, help="approve dangerous tool calls automatically")
    parser.add_argument("--show-reasoning", action="store_true", default=None, help="print reasoning chunks")
    parser.add_argument("--list-sessions", action="store_true", default=None, help="list stored sessions and exit")
    return parser


async def run(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser() if args.home else Path(composition.DEFAULT_HOME).expanduser()
    options.LAUNCH.clear()
    options.LAUNCH.set(
        cwd=args.cwd,
        resume=args.resume,
        model=args.model,
        provider=args.provider,
        sessionDir=args.session_dir or (str(home / "sessions") if args.home else None),
        autoApprove=True if args.yolo else None,
        showReasoning=True if args.show_reasoning else None,
        listSessions=True if args.list_sessions else None,
    )

    base = resolve_config_path(args.config)
    extra = [Path(path).expanduser() for path in args.patch]
    for path in extra:
        if not path.is_file():
            print(f"[error] patch file not found: {path}", file=sys.stderr)
            return 1
    # Ambient layers are optional and can be switched off; an explicit --patch
    # is a direct instruction and always applies, last.
    ambient = [] if args.no_patches else layer_patches(home=home, project=Path.cwd())
    patches = ambient + extra

    if args.dump_config:
        rows, applied = composition.compose(base, patches)
        sys.stdout.write(composition.dump(rows, base=base, patches=applied))
        return 0

    ctx = Context()

    # Registered before the composition mounts: a front end may finish during
    # startup (``--list-sessions``, piped stdin) and that exit must be seen.
    stopped = asyncio.Event()
    state = {"code": 0}

    def on_exit(code: object = 0) -> None:
        state["code"] = int(code) if code is not None else 0  # type: ignore[arg-type]
        stopped.set()

    ctx.on("app/exit", on_exit)

    await load_layers(ctx, base, patches, composed_path=home / "composed.yml")

    failed = failed_fibers(ctx)
    if failed:
        for fiber in failed:
            print(f"[error] {fiber.name} FAILED: {fiber.error}", file=sys.stderr)
        return 1

    if ctx.get("repl") is None:
        # No interactive front end: mirror the composition-runner contract —
        # exit at once when nothing keeps the event loop busy, otherwise wait
        # for a signal.
        busy = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        if not busy:
            return 0
        print(f"[cdh] {len(collect_fibers(ctx))} fiber(s) active; press Ctrl-C to stop", file=sys.stderr)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stopped.set)
            except (NotImplementedError, RuntimeError):  # pragma: no cover - platform dependent
                pass

    await stopped.wait()
    return state["code"]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.config = Path(args.config) if args.config else None
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:  # pragma: no cover - terminal interrupt
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
