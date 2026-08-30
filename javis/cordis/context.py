"""Context: root and child dependency containers for plugins.

Port of ``vendor/cordis/src/context.ts`` adapted to an explicit ``ctx.get()``
design (no proxy magic): the context is a plain class, services are read via
``ctx.get(name)`` and written via ``ctx.provide``/``ctx.set``; every event,
registry and lifecycle method is a real method that forwards to the shared
core services (``ctx.events``, ``ctx.registry``, ``ctx.reflect``,
``ctx.logger``) passing this context, so ownership (which fiber registered
what) resolves naturally.

``extend()``, ``isolate()`` and ``intercept()`` create scoped child contexts
without mutating the parent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from .events import EventsService
from .fiber import Fiber
from .logger import LoggerService
from .reflect import ReflectService
from .registry import RegistryService
from .scope import InterceptMap, IsolationMap

if TYPE_CHECKING:
    pass


class Context:
    """Root and child dependency containers for Cordis plugins."""

    def __init__(self) -> None:
        """Create the root context and install the built-in services."""
        self._parent: Context | None = None
        self.root: Context = self
        self.baseUrl: str | None = None
        self._isolate = IsolationMap()
        self._intercept = InterceptMap()
        self._meta: dict[str, Any] = {}
        # Accessor/mixin property declarations scoped to this context; child
        # contexts resolve them through the `_parent` chain (children shadow
        # ancestors, siblings are independent).
        self._props: dict[str, dict[str, Any]] = {}

        self.registry = RegistryService(self)
        self.fiber = Fiber(self, {}, {}, None)  # root fiber (runtime = None)
        self.events = EventsService(self)
        self.logger = LoggerService(self)
        self.reflect = ReflectService(self)
        # Discard bootstrap effects (they must outlive the root fiber).
        self.fiber._effects.clear()

    # -- scoping ------------------------------------------------------------

    @classmethod
    def _child(cls, parent: "Context", meta: dict[str, Any] | None = None) -> "Context":
        obj = cls.__new__(cls)
        # Inherit every parent attribute (JS prototype inheritance), then
        # re-derive the machinery for this child scope.
        for key, value in parent.__dict__.items():
            setattr(obj, key, value)
        obj._parent = parent
        obj.root = parent.root
        obj.baseUrl = parent.baseUrl
        obj._isolate = IsolationMap(parent._isolate)
        obj._intercept = InterceptMap(parent._intercept)
        obj.events = parent.events
        obj.registry = parent.registry
        obj.reflect = parent.reflect
        obj.logger = parent.logger
        obj.fiber = parent.fiber
        obj._props = {}  # own declarations only; ancestors resolve via _parent
        obj._meta = dict(meta or {})
        for key, value in (meta or {}).items():
            setattr(obj, key, value)
        return obj

    def extend(self, meta: dict[str, Any] | None = None) -> "Context":
        """Create a child context with extra metadata on top of this scope.

        The child inherits every property of this context; own properties of
        ``meta`` shadow the inherited ones. The parent is not mutated.
        """
        return Context._child(self, meta)

    def isolate(self, name: str, label: object | None = None) -> "Context":
        """Create a child context with an independent service scope for ``name``.

        Below the returned context, reads and writes of ``name`` resolve
        against the new label instead of the parent's. Passing the same label
        to two ``isolate()`` calls joins their scopes.
        """
        child = self.extend()
        child._isolate = IsolationMap(self._isolate)
        child._isolate.override(name, label if label is not None else object())
        return child

    def intercept(self, name: str, config: Any) -> "Context":
        """Add service-specific intercept config for plugins started below.

        Plugins loaded under the returned context see ``config`` merged into
        the service's resolved config (ancestor entries first).
        """
        child = self.extend()
        child._intercept = InterceptMap(self._intercept)
        child._intercept.set(name, config)
        return child

    @staticmethod
    def is_context(value: Any) -> bool:
        """Return ``True`` for Cordis context objects (``Context.is`` in JS)."""
        return isinstance(value, Context)

    # -- service store ------------------------------------------------------

    def get(self, name: str, strict: bool = True) -> Any:
        """Read a service from the store without the inject requirement.

        Returns ``None`` when not (yet) provided. With ``strict`` (default)
        only implementations whose providing fiber is ACTIVE are returned.
        """
        return self.reflect.get(self, name, strict)

    def set(self, name: str, value: Any) -> bool:
        """Overwrite a provided service's value (provider fiber only)."""
        return self.reflect.set(self, name, value)

    def provide(self, name: str, value: Any = None, check: Callable[[Any], bool] | None = None) -> Callable[[], Any]:
        """Register a service implementation owned by the current fiber.

        Returns a disposer that unregisters the service.
        """
        return self.reflect.provide(self, name, value, check)

    def accessor(
        self,
        name: str,
        get: Callable[["Context"], Any],
        set: Callable[["Context", Any], bool] | None = None,
    ) -> Callable[[], Any]:
        """Define a computed context property backed by get/set hooks."""
        return self.reflect.accessor(self, name, get, set)

    def mixin(self, source: Any, mixins: Any) -> Callable[[], Any]:
        """Expose selected members of a service directly on ``ctx``."""
        return self.reflect.mixin(self, source, mixins)

    # -- events -------------------------------------------------------------

    def on(self, name: str, listener: Callable[..., Any], options: Any = None) -> Callable[[], bool]:
        """Register an event listener owned by the current fiber."""
        return self.events.on(self, name, listener, options)

    def once(self, name: str, listener: Callable[..., Any], options: Any = None) -> Callable[[], bool]:
        """Like :meth:`on`, but the listener disposes itself after its first call."""
        return self.events.once(self, name, listener, options)

    def emit(self, name: str, *args: Any) -> None:
        """Synchronously dispatch an event, ignoring listener return values."""
        self.events.emit(name, *args)

    async def parallel(self, name: str, *args: Any) -> None:
        """Run all listeners concurrently and wait for every one to settle."""
        await self.events.parallel(name, *args)

    async def serial(self, name: str, *args: Any) -> Any:
        """Await listeners in order until one returns a bail value."""
        return await self.events.serial(name, *args)

    def bail(self, name: str, *args: Any) -> Any:
        """Call listeners synchronously until one returns a bail value."""
        return self.events.bail(name, *args)

    def waterfall(self, name: str, *args: Any) -> Any:
        """Dispatch a waterfall event; the last argument is the innermost ``next``."""
        return self.events.waterfall(self, name, *args)

    # -- registry -----------------------------------------------------------

    def plugin(self, plugin: Any, config: Any = None) -> Fiber:
        """Load a plugin in the current context; returns its (awaitable) fiber."""
        return self.registry.plugin(self, plugin, config)

    def inject(self, deps: Any, callback: Callable[..., Any]) -> Fiber:
        """Run a callback once the requested services are available."""
        return self.registry.inject(self, deps, callback)

    # -- effects ------------------------------------------------------------

    def effect(self, execute: Callable[[], Any], label: str = "anonymous") -> Callable[[], Any]:
        """Register a cleanup-aware effect on the current fiber."""
        return self.fiber.effect(execute, label)

    def __repr__(self) -> str:
        return f"<Context {self.fiber.name}>"
