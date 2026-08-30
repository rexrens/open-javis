"""Command-line entry: ``python -m javis.cordis.cli run [cordis.yml]``.

Creates a root context, mounts the :class:`Loader` on the composition, waits
for the initial load to settle, then:

- exits 1 when any fiber FAILED (config or startup errors are fatal),
- exits 0 when nothing keeps the event loop busy (mirrors the Cordis
  tutorial's "process exits when nothing is running"),
- otherwise keeps running until SIGINT (``--wait`` forces waiting).
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Any

from . import Context, FiberState
from .loader import Loader
from .registry import settle


def collect_fibers(ctx: Context) -> list[Any]:
    return [fiber for runtime in ctx.registry.values() for fiber in list(runtime.fibers)]


async def run(args: argparse.Namespace) -> int:
    ctx = Context()
    ctx.baseUrl = str(args.file.parent)
    loader_fiber = ctx.plugin(Loader, {"file": str(args.file)})
    try:
        await loader_fiber
    except BaseException as error:
        print(f"[error] loader failed: {error}", file=sys.stderr)
        return 1
    await settle(ctx)

    fibers = collect_fibers(ctx)
    failed = [f for f in fibers if f.state == FiberState.FAILED]
    if failed:
        for fiber in failed:
            print(f"[error] {fiber.name} FAILED: {fiber._error}", file=sys.stderr)
        return 1

    busy_tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if not args.wait and not busy_tasks:
        return 0

    print(f"[dshlike] {len(fibers)} fiber(s) active; press Ctrl-C to stop", file=sys.stderr)
    stop = asyncio.Event()
    exit_code = {"code": 0}

    def on_exit(code: Any = 0) -> None:
        exit_code["code"] = int(code) if code is not None else 0
        stop.set()

    # Documented extension: plugins may request a graceful exit.
    ctx.on("app/exit", on_exit)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover
            pass
    await stop.wait()
    return exit_code["code"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dshlike", description="dsh-like composition runner")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="run a cordis.yml composition")
    run_parser.add_argument("file", type=str, nargs="?", default="cordis.yml")
    run_parser.add_argument("--wait", action="store_true", help="keep running until Ctrl-C")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        args.file = Path(args.file)
        return asyncio.run(run(args))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
