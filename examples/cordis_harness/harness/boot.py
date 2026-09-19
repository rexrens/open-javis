"""Composition boot helpers shared by the CLI and the tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterable

from javis.cordis import Context, FiberState

from . import composition

#: The composition shipped inside the package.
PACKAGED_CONFIG = Path(__file__).resolve().parent / "cordis.yml"

#: Where the merged composition is written whenever patches are in play.
COMPOSED_CONFIG = composition.DEFAULT_HOME + "/composed.yml"


def resolve_config_path(explicit: str | None = None) -> Path:
    """``--config`` wins, then ``./cordis.yml``, then the packaged default."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    local = Path.cwd() / "cordis.yml"
    if local.is_file():
        return local.resolve()
    return PACKAGED_CONFIG


def layer_patches(
    *,
    home: str | Path | None = composition.DEFAULT_HOME,
    project: str | Path | None = None,
    extra: Iterable[str | Path] = (),
) -> list[Path]:
    """The patch layers that exist right now, in application order.

    Ambient layers (the harness home and the working directory) are optional:
    a missing file contributes nothing. Explicit ``--patch`` files are checked
    by the caller, so a typo fails loudly instead of being ignored.
    """
    candidates = composition.patch_paths(home=home, project=project, extra=extra)
    return [path for path in candidates if path.is_file()]


def collect_fibers(ctx: Context) -> list[Any]:
    """Every live fiber across all runtimes."""
    return [fiber for runtime in ctx.registry.values() for fiber in list(runtime.fibers)]


def failed_fibers(ctx: Context) -> list[Any]:
    return [fiber for fiber in collect_fibers(ctx) if fiber.state == FiberState.FAILED]


async def settle(ctx: Context) -> None:
    """Wait until every fiber has settled into a stable state.

    Loading is dependency-driven and asynchronous: after mounting a
    composition, dependents are still starting, so callers await this before
    reading services.
    """
    while True:
        in_flight = [
            fiber.inertia
            for runtime in ctx.registry.values()
            for fiber in list(runtime.fibers)
            if fiber.inertia is not None
        ]
        if not in_flight:
            await asyncio.sleep(0.05)
            in_flight = [
                fiber.inertia
                for runtime in ctx.registry.values()
                for fiber in list(runtime.fibers)
                if fiber.inertia is not None
            ]
            if not in_flight:
                return
        await asyncio.gather(*in_flight, return_exceptions=True)


async def load_composition(config_path: str | Path) -> tuple[Context, Any]:
    """Create a root context and mount ``config_path``'s composition on it.

    This is the library entry point: it mounts exactly the file it is given,
    without the ambient layers ``cdh`` applies (use :func:`load_layers`).
    """
    ctx = Context()
    fiber = await mount(ctx, config_path)
    return ctx, fiber


async def mount(ctx: Context, config_path: str | Path) -> Any:
    """Mount a composition on ``ctx`` and settle; returns the loader fiber.

    A composition-level failure (an entry whose module cannot be imported)
    fails the *loader* fiber, so nothing is raised here: the error stays on
    ``fiber.error`` and :func:`failed_fibers` reports it. Callers decide
    whether a failed composition is fatal.
    """
    from javis.cordis import Loader

    path = Path(config_path).expanduser().resolve()
    ctx.baseUrl = str(path.parent)
    fiber = ctx.plugin(Loader, {"file": str(path)})
    try:
        await fiber
    except BaseException:  # noqa: BLE001 - recorded on the fiber
        pass
    await settle(ctx)
    return fiber


async def load_layers(
    ctx: Context,
    base_path: str | Path,
    patches: Iterable[str | Path] = (),
    *,
    composed_path: str | Path | None = COMPOSED_CONFIG,
) -> tuple[Any, list[Path]]:
    """Mount ``base_path`` with ``patches`` merged over it.

    With no patches the base file is mounted directly, so a plain run never
    writes a generated file. With patches, the merged rows are written to
    ``composed_path`` (the loader reads a file) and that file is mounted.
    Returns the loader fiber and the layers that contributed.
    """
    layers = [Path(path).expanduser().resolve() for path in patches]
    if not layers:
        return await mount(ctx, base_path), []
    rows, applied = composition.compose(base_path, layers)
    target = composition.write(rows, composed_path or COMPOSED_CONFIG, base=base_path, patches=applied)
    return await mount(ctx, target), applied
