"""Plugin registry: plugin shapes, runtimes, dependency-driven loading.

Port of ``vendor/cordis/src/registry.ts``. ``ctx.plugin()`` normalizes the
three plugin shapes (function, object with ``apply``, Service subclass),
reuses one ``Runtime`` record per callback, and starts a new :class:`Fiber`
under the current context. Loading is dependency-driven: a fiber stays
PENDING until every service in its ``inject`` map is provided by an ACTIVE
fiber in the same isolation scope.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .fiber import Fiber

if TYPE_CHECKING:
    from .context import Context


def is_applicable(plugin: Any) -> bool:
    return plugin is not None and not callable(plugin) and callable(getattr(plugin, "apply", None))


def resolve_inject(inject: Any, result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize an array/object inject declaration into a name -> config map."""
    if result is None:
        result = {}
    if not inject:
        return result
    if isinstance(inject, (list, tuple, set)):
        for name in inject:
            result[name] = None
    elif isinstance(inject, dict):
        for name, cfg in inject.items():
            result[name] = cfg if cfg is not None else None
    else:
        raise TypeError(f"invalid inject declaration: {inject!r}")
    return result


@dataclass
class Runtime:
    """Mutable registry record shared by all fibers of one plugin callback."""

    name: str | None = None
    callback: Any = None
    fibers: list[Fiber] = field(default_factory=list)
    Config: Any = None


def plugin_attr(plugin: Any, key: str, default: Any = None) -> Any:
    """Read plugin metadata from a dict plugin, an object plugin or a function."""
    if isinstance(plugin, dict):
        return plugin.get(key, default)
    return getattr(plugin, key, default)


class RegistryService:
    """Plugin registry installed as ``ctx.registry``."""

    def __init__(self, ctx: "Context"):
        self.ctx = ctx
        self._counter = 0
        self._runtimes: dict[Any, Runtime] = {}

    @property
    def counter(self) -> int:
        self._counter += 1
        return self._counter

    @property
    def size(self) -> int:
        return len(self._runtimes)

    def resolve(self, plugin: Any) -> Any:
        """Resolve a supported plugin shape to its executable callback."""
        try:
            if callable(plugin):
                return plugin
            if isinstance(plugin, dict) and callable(plugin.get("apply")):
                return plugin["apply"]
            if is_applicable(plugin):
                return plugin.apply
        except BaseException:
            pass
        return None

    def get(self, plugin: Any) -> Runtime | None:
        key = self.resolve(plugin)
        return self._runtimes.get(key) if key is not None else None

    def has(self, plugin: Any) -> bool:
        key = self.resolve(plugin)
        return key is not None and key in self._runtimes

    def has_callback(self, callback: Any) -> bool:
        return callback in self._runtimes

    def delete(self, plugin: Any) -> Runtime | None:
        key = self.resolve(plugin)
        runtime = self._runtimes.pop(key, None) if key is not None else None
        if runtime is None:
            return None
        for fiber in list(runtime.fibers):
            asyncio_ensure(fiber.dispose)
        return runtime

    def keys(self) -> Iterable[Any]:
        return self._runtimes.keys()

    def values(self) -> Iterable[Runtime]:
        return self._runtimes.values()

    def entries(self) -> Iterable[tuple[Any, Runtime]]:
        return self._runtimes.items()

    def fibers(self) -> list[Fiber]:
        """Every live fiber across all runtimes (diagnostics)."""
        return [fiber for runtime in self._runtimes.values() for fiber in list(runtime.fibers)]

    def pending(self) -> list[Fiber]:
        """Fibers waiting for required services (diagnostics)."""
        from .fiber import FiberState

        return [f for f in self.fibers() if f.state == FiberState.PENDING]

    # -- loading ------------------------------------------------------------

    def inject(self, ctx: "Context", deps: Any, callback: Callable[..., Any]) -> Fiber:
        """Run a callback once the requested services are available.

        Shorthand for ``ctx.plugin({inject: deps, apply: callback})``.
        """
        return self.plugin(ctx, {"inject": deps, "apply": callback, "name": getattr(callback, "__name__", None)}, None)

    def plugin(self, ctx: "Context", plugin: Any, config: Any = None) -> Fiber:
        """Start a plugin in the current context and return its fiber."""
        callback = self.resolve(plugin)
        if callback is None:
            raise TypeError(
                'invalid plugin, expect function or object with an "apply" method, received '
                + type(plugin).__name__
            )
        ctx.fiber.assertActive()

        runtime = self._runtimes.get(callback)
        if runtime is None:
            name = plugin_attr(plugin, "name")
            if name is None and callable(plugin):
                # Python functions have __name__, not `.name` (JS Function.name).
                name = getattr(plugin, "__name__", None)
                if name == "<lambda>":
                    # Anonymous callables have no usable display name; let the
                    # fiber inherit the nearest named ancestor (JS: '' is falsy).
                    name = None
            if name == "apply":
                name = None
            runtime = Runtime(
                name=name,
                callback=callback,
                Config=plugin_attr(plugin, "Config"),
            )
            self._runtimes[callback] = runtime

        fiber = Fiber(ctx, config, resolve_inject(plugin_attr(plugin, "inject")), runtime)
        return fiber


def asyncio_ensure(disposer: Callable[[], Any]) -> None:
    """Safely drive an async disposer without awaiting (used by ``delete``)."""
    import asyncio

    try:
        result = disposer()
        if result is not None and hasattr(result, "__await__"):
            asyncio.ensure_future(result)
    except BaseException:
        pass


async def settle(ctx: "Context") -> None:
    """Wait until every fiber has settled into a stable state.

    Mirrors the Cordis tutorial's checkpoint: after mounting a composition,
    dependent loads triggered by provider activations run asynchronously, so
    callers await this before reading provided services.
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
