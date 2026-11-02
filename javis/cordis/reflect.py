"""Reflection and service-resolution layer (``ctx.reflect``).

Port of ``vendor/cordis/src/reflect.ts``. Unlike the JavaScript version, the
Python context is a plain class (no proxy magic): ``ctx.get(name)`` is the
explicit way to read a service, ``ctx.provide``/``ctx.set``/``ctx.accessor``/
``ctx.mixin`` the ways to write them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from .fiber import FiberState

if TYPE_CHECKING:
    from .context import Context
    from .fiber import Fiber


@dataclass
class Impl:
    """Concrete service implementation record stored in the root reflect service."""

    name: str
    value: Any
    fiber: "Fiber"
    check: Callable[[Any], bool] | None = None


class _ServiceFilterCtx:
    """Ephemeral context carrying a listener filter for ``internal/service``.

    Stands in for the JavaScript ``this``-bound filter context created by
    ``ReflectService.notify``.
    """

    __slots__ = ("_isolate", "filter")

    def __init__(self, filter_fn: Callable[["Context"], bool]):
        self._isolate = object()
        self.filter = filter_fn


class ReflectService:
    """Service store and context property declarations, keyed by isolation label."""

    def __init__(self, ctx: "Context"):
        self.ctx = ctx
        self.store: dict[object, Impl] = {}  # isolation label -> Impl

    @staticmethod
    def _find_prop(ctx: "Context", name: str) -> dict[str, Any] | None:
        """Find the nearest declaration of ``name`` on ``ctx`` or its ancestors.

        Accessors are scoped to the context that declared them: children
        inherit ancestor declarations (own properties shadow inherited ones)
        while sibling contexts stay independent.
        """
        current: Context | None = ctx
        while current is not None:
            prop = current._props.get(name)
            if prop is not None:
                return prop
            current = current._parent
        return None

    # -- reads --------------------------------------------------------------

    def _get_impl(self, ctx: "Context", name: str, strict: bool = True) -> Impl | None:
        label = ctx._isolate.get(name)
        if label is None:
            return None
        impl = self.store.get(label)
        if impl is None:
            return None
        if strict and impl.fiber.state != FiberState.ACTIVE:
            return None
        return impl

    def get(self, ctx: "Context", name: str, strict: bool = True) -> Any:
        """Read a service without the inject requirement.

        Returns ``None`` when not (yet) provided. With ``strict`` (default),
        only implementations whose providing fiber is ACTIVE are returned.
        """
        prop = self._find_prop(ctx, name)
        if prop is not None and prop["type"] == "accessor":
            return prop["get"](ctx)
        impl = self._get_impl(ctx, name, strict)
        return impl.value if impl is not None else None

    # -- writes -------------------------------------------------------------

    def set(self, ctx: "Context", name: str, value: Any) -> bool:
        """Overwrite a provided service's value, or route through an accessor.

        Only the fiber that provided the service may set it; setting an
        unprovided name raises. Accessor properties are written through their
        ``set`` hook when one is declared (without the proxy, this is the only
        write path for accessors); a setter-less accessor raises.
        """
        prop = self._find_prop(ctx, name)
        if prop is not None and prop["type"] == "accessor":
            setter = prop.get("set")
            if setter is None:
                raise RuntimeError(f'cannot set property "{name}": accessor has no setter')
            return setter(ctx, value) is not False
        label = ctx._isolate.get(name)
        impl = self.store.get(label) if label is not None else None
        if impl is None:
            raise RuntimeError(f'cannot set property "{name}" without provide')
        if impl.fiber is not ctx.fiber:
            raise RuntimeError(f'cannot set property "{name}" in multiple fibers')
        impl.value = value
        return True

    def provide(
        self,
        ctx: "Context",
        name: str,
        value: Any = None,
        check: Callable[[Any], bool] | None = None,
    ) -> Callable[[], Any]:
        """Register a service implementation owned by the current fiber.

        The service becomes visible to dependents in the same isolation scope
        once the fiber is ACTIVE; it is unregistered when the returned
        disposer runs or the fiber unloads. Throws if the name is already
        provided in this scope or declared as an accessor.
        """
        return ctx.fiber.effect(
            lambda: self._provide(ctx, name, value, check),
            f"ctx.provide({name!r})",
        )

    def _provide(
        self,
        ctx: "Context",
        name: str,
        value: Any,
        check: Callable[[Any], bool] | None,
    ) -> Callable[[], Any]:
        prop = self._find_prop(ctx, name)
        if prop is not None and prop["type"] != "service":
            raise RuntimeError(f'property "{name}" is already declared as {prop["type"]}')

        ctx.root._isolate.ensure(name)
        label = ctx._isolate.get(name)
        if label in self.store:
            raise RuntimeError(
                f'service "{name}" has been registered at <{self.store[label].fiber.name}>'
            )
        impl = Impl(name=name, value=value, fiber=ctx.fiber, check=check)
        self.store[label] = impl
        ctx.fiber._store_provided[name] = impl
        if ctx.fiber.state == FiberState.ACTIVE:
            self.notify(ctx, [name])

        async def disposer() -> None:
            self.store.pop(label, None)
            fibers = self.notify(ctx, [name])
            await asyncio.gather(*(f.await_() for f in fibers), return_exceptions=True)
            # ensure self access before dependency cleanup
            ctx.fiber._store_provided.pop(name, None)

        return disposer

    def accessor(
        self,
        ctx: "Context",
        name: str,
        get: Callable[["Context"], Any],
        set: Callable[["Context", Any], bool] | None = None,
    ) -> Callable[[], Any]:
        """Define a computed context property backed by get/set hooks."""
        return ctx.fiber.effect(
            lambda: self._accessor(ctx, name, get, set),
            f"ctx.accessor({name!r})",
        )

    def _accessor(
        self,
        ctx: "Context",
        name: str,
        get: Callable[["Context"], Any],
        set: Callable[["Context", Any], bool] | None,
    ) -> Callable[[], bool]:
        # Same-context redeclaration conflicts; an ancestor declaration is
        # shadowed by this own property (JS `defineProperty` semantics).
        if name in ctx._props:
            raise RuntimeError(f'property "{name}" is already declared as {ctx._props[name]["type"]}')
        # A service currently visible in this scope also conflicts, matching
        # the provide/accessor symmetry of the original reflect service.
        if self.store.get(ctx._isolate.get(name)) is not None:
            raise RuntimeError(f'property "{name}" is already declared as service')
        ctx._props[name] = {"type": "accessor", "get": get, "set": set}

        def disposer() -> bool:
            return ctx._props.pop(name, None) is not None

        return disposer

    def mixin(self, ctx: "Context", source: Any, mixins: Any) -> Callable[[], Any]:
        """Expose selected members of a service directly on ``ctx``.

        Each mixed-in key becomes an accessor that forwards to the source
        service (bound to it). Mixins are removed when the current fiber
        unloads.
        """
        entries = list(mixins.items()) if isinstance(mixins, dict) else [(k, k) for k in mixins]
        disposers: list[Callable[[], Any]] = []

        def body() -> None:
            for key, target in entries:
                disposers.append(self.accessor(ctx, target, self._mixin_get(source, key), self._mixin_set(source, key)))

        ctx.fiber.effect(body, f"ctx.mixin({source!r})")

        def disposer() -> Any:
            return asyncio.gather(*(d() for d in disposers), return_exceptions=True)

        return disposer

    @staticmethod
    def _mixin_get(source: Any, key: str) -> Callable[["Context"], Any]:
        def get(ctx: "Context") -> Any:
            service = source if not isinstance(source, str) else ctx.get(source)
            if service is None:
                return None
            value = getattr(service, key)
            if callable(value):
                return getattr(service, key)
            return value

        return get

    @staticmethod
    def _mixin_set(source: Any, key: str) -> Callable[["Context", Any], bool]:
        def set(ctx: "Context", value: Any) -> bool:
            service = source if not isinstance(source, str) else ctx.get(source)
            if service is None:
                return False
            setattr(service, key, value)
            return True

        return set

    # -- dependency notification -------------------------------------------

    def notify(
        self,
        ctx: "Context",
        names: list[str],
        filter_fn: Callable[["Context", str], bool] | None = None,
    ) -> list["Fiber"]:
        """Re-evaluate every fiber that requires one of the given services.

        Returns the fibers whose dependency state was refreshed, and emits the
        ``internal/service`` event per name (scope-filtered) so observers can
        watch service registration/unregistration.
        """
        if filter_fn is None:
            def filter_fn(fiber_ctx: "Context", name: str) -> bool:
                return fiber_ctx._isolate.get(name) == ctx._isolate.get(name)
        affected: list[Fiber] = []
        for runtime in self.ctx.registry._runtimes.values():
            for fiber in list(runtime.fibers):
                has_update = False
                for name in names:
                    if name not in fiber.inject:
                        continue
                    if not filter_fn(fiber.ctx, name):
                        continue
                    has_update = True
                    fiber._check_impl(name)
                if not has_update:
                    continue
                fiber._refresh()
                affected.append(fiber)

        # Emit internal/service per changed name, filtered to the scope of
        # `ctx` (mirrors Cordis: an ephemeral filter context as `this`).
        for name in names:
            value = None
            impl = self._get_impl(ctx, name, strict=False)
            if impl is not None:
                value = impl.value

            # Bind `name` at definition time (default argument) so the filter
            # closure does not capture the loop variable by reference.
            def emit_filter(target: Context, _name: str = name) -> bool:
                return filter_fn(target, _name)

            self.ctx.events.emit(
                _ServiceFilterCtx(emit_filter),
                "internal/service",
                name,
                value,
            )
        return affected
