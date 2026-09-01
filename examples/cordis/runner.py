#!/usr/bin/env python
"""Universal runner for the ``examples/cordis`` tutorial chapters.

Every chapter is a directory with a ``cordis.yml`` composition. This script
boots a root :class:`~javis.cordis.Context`, mounts the chapter's composition
on the :class:`~javis.cordis.loader.Loader`, waits for the initial load to
settle, then:

- exits 1 when any fiber FAILED (config / apply errors are fatal),
- exits 0 when nothing keeps the event loop busy,
- keeps running until Ctrl-C with ``--wait`` (required by the ``hmr``
  chapter, whose watcher task owns the loop).

Usage (from the repo root)::

    uv run python examples/cordis/runner.py hello        # chapter 1
    uv run python examples/cordis/runner.py events       # chapter 6
    uv run python examples/cordis/runner.py hmr --wait   # keep alive for hot reload

This is the same boot sequence the ``examples/dsh_harness/cli.py`` demo uses
(inlined there so the demo is self-contained); keeping it here gives every
tutorial chapter the identical, minimal composition bootstrap.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Any

from javis.cordis import Context, FiberState
from javis.cordis.loader import Loader
from javis.cordis.registry import settle

_CORDIS_ROOT = Path(__file__).resolve().parent


def collect_fibers(ctx: Context) -> list[Any]:
    return [fiber for runtime in ctx.registry.values() for fiber in list(runtime.fibers)]


async def run(chapter: str, wait: bool) -> int:
    chapter_dir = _CORDIS_ROOT / chapter
    compose_file = chapter_dir / "cordis.yml"
    if not compose_file.exists():
        print(f"[error] no such chapter: {chapter} ({compose_file})", file=sys.stderr)
        return 2

    ctx = Context()
    ctx.baseUrl = str(chapter_dir)
    # The chapter directory becomes the plugin import context: plugins loaded
    # from relative paths can import sibling modules (e.g. failure/good.py).
    if str(chapter_dir) not in sys.path:
        sys.path.insert(0, str(chapter_dir))

    loader_fiber = ctx.plugin(Loader, {"file": str(compose_file)})
    try:
        await loader_fiber
    except BaseException as error:  # noqa: BLE001 — report and exit 1
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
    if not wait and not busy_tasks:
        return 0

    print(f"[runner] {len(fibers)} fiber(s) active; press Ctrl-C to stop", file=sys.stderr)
    stop = asyncio.Event()

    # Documented extension: plugins may request a graceful exit (see the
    # lifecycle chapter) — ``ctx.emit("app/exit", code)``.
    exit_code = {"code": 0}
    ctx.on("app/exit", lambda code: (exit_code.update(code=int(code or 0)), stop.set()))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover
            pass
    await stop.wait()
    return exit_code["code"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cordis-runner",
        description="Run one examples/cordis tutorial chapter composition",
    )
    parser.add_argument("chapter", type=str, help="chapter directory name (e.g. hello, events)")
    parser.add_argument(
        "--wait",
        action="store_true",
        help="keep running until Ctrl-C (required by hmr, useful for lifecycle)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(run(args.chapter, args.wait))


if __name__ == "__main__":
    raise SystemExit(main())
