"""Plugin context — service registry, cross-plugin events, lifecycle hooks.

- ``ServiceRegistry``: shared across plugins; ``provide`` wakes waiting
  instances (asyncio.Condition); owners let unload revoke exactly its services.
- ``EventBus``: internal broadcast table (implementation detail, not part of
  the public API). Listener lifetime is owned by ``ctx.effect`` disposers, so
  unload removes exactly that plugin's listeners — no owner bookkeeping.
- ``PluginContext``: the ``ctx`` handed to ``apply(ctx, config)``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast, overload

from pydantic import BaseModel

Disposer = Callable[[], Awaitable[None] | None]
EventHandler = Callable[[Any], Awaitable[None] | None]
StartHook = Callable[[], Awaitable[None] | None]

T = TypeVar("T")

def _validate_service(name: str, value: Any, value_type: type[T]) -> T:
    """Validate a retrieved service against ``value_type``.

    - pydantic ``BaseModel`` → ``model_validate`` (data payloads are coerced
      and validated);
    - any other type → ``isinstance`` check (``TypeError`` on mismatch).
    """
    if issubclass(value_type, BaseModel):
        return cast(T, value_type.model_validate(value))
    if not isinstance(value, value_type):
        raise TypeError(
            f"Service {name!r} has type {type(value).__name__}, "
            f"expected {value_type.__name__}"
        )
    return value


class ServiceRegistry:
    """Cross-plugin service store with dependency-wakeup."""

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}
        self._owners: dict[str, str] = {}
        self._cond = asyncio.Condition()

    def provide(self, name: str, value: Any, owner: str | None = None) -> None:
        self._services[name] = value
        if owner is not None:
            self._owners[name] = owner
        try:
            asyncio.get_running_loop().create_task(self._notify())
        except RuntimeError:
            pass  # no running loop — nothing to wake

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()

    @overload
    def get(self, name: str) -> Any: ...

    @overload
    def get(self, name: str, value_type: type[T]) -> T | None: ...

    def get(self, name: str, value_type: type[T] | None = None) -> T | None:
        """Fetch a service by name (``None`` when missing).

        With ``value_type`` the value is validated on retrieval (see
        ``_validate_service``).
        """
        if name not in self._services:
            return None
        value = self._services[name]
        if value_type is None:
            return cast(T | None, value)
        return _validate_service(name, value, value_type)

    def contains(self, name: str) -> bool:
        return name in self._services

    def owner_of(self, name: str) -> str | None:
        """Plugin that provided ``name`` (``None`` for built-ins / unknown)."""
        return self._owners.get(name)

    def owners(self) -> dict[str, str]:
        """Copy of the service → owner plugin map (plugin-provided only)."""
        return dict(self._owners)

    async def wait_for(self, names: list[str], timeout: float) -> list[str]:
        """Wait until all ``names`` are provided (or timeout); return missing."""
        def _ready() -> bool:
            return all(n in self._services for n in names)

        if _ready():
            return []
        try:
            async with self._cond:
                await asyncio.wait_for(self._cond.wait_for(_ready), timeout)
        # asyncio.TimeoutError is a distinct type on Python 3.10, so it must be
        # caught explicitly; UP041 (py311-targeted) wrongly treats it as an alias.
        except asyncio.TimeoutError:  # noqa: UP041
            pass
        return [n for n in names if n not in self._services]

    def revoke_owner(self, owner: str) -> None:
        for name in [n for n, o in self._owners.items() if o == owner]:
            del self._services[name]
            del self._owners[name]


class EventBus:
    """Cross-plugin event dispatch (emit = fire-and-forget, emit_serial = await).

    Internal implementation detail: listener cleanup is delegated to the
    disposers ``PluginContext.on`` registers, so this class carries no owner
    concept.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[EventHandler]] = {}

    def on(self, event: str, handler: EventHandler) -> Callable[[], None]:
        self._listeners.setdefault(event, []).append(handler)

        def cancel() -> None:
            handlers = self._listeners.get(event)
            if not handlers:
                return
            if handler in handlers:
                handlers.remove(handler)
            if not handlers:
                del self._listeners[event]

        return cancel

    def emit(self, event: str, payload: Any = None) -> None:
        for handler in list(self._listeners.get(event, ())):
            result = handler(payload)
            if inspect.isawaitable(result):
                asyncio.get_running_loop().create_task(_consume(result, event))

    async def emit_serial(self, event: str, payload: Any = None) -> None:
        for handler in list(self._listeners.get(event, ())):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result


async def _consume(awaitable: Awaitable[None], event: str) -> None:
    try:
        await awaitable
    except Exception:  # fire-and-forget handlers must not crash the loop
        logging.getLogger("javis.plugins").exception(
            "event handler for %r failed", event
        )


class PluginContext:
    """Per-plugin context handed to ``apply(ctx, config)``."""

    def __init__(
        self,
        *,
        name: str,
        config: Any,
        services: ServiceRegistry,
        bus: EventBus | None = None,
        javis_config: Any = None,
    ) -> None:
        self.name = name
        self.config = config
        self.javis_config = javis_config
        self.logger = logging.getLogger(f"javis.plugins.{name}")
        self._services = services
        self._bus = bus if bus is not None else EventBus()
        self._disposers: list[Disposer] = []
        self._start_hooks: list[StartHook] = []

    # ---- services -------------------------------------------------------
    def provide(self, name: str, value: Any) -> None:
        self._services.provide(name, value, owner=self.name)

    @overload
    def get(self, name: str) -> Any: ...

    @overload
    def get(self, name: str, value_type: type[T]) -> T | None: ...

    def get(self, name: str, value_type: type[T] | None = None) -> T | None:
        """Fetch a service, optionally validating it against ``value_type``."""
        value = self._services.get(name)
        if value is None and not self._services.contains(name):
            raise KeyError(f"Service {name!r} not provided")
        if value_type is None:
            return cast(T | None, value)
        return _validate_service(name, value, value_type)

    # ---- events ---------------------------------------------------------
    def on(self, event: str, handler: EventHandler) -> Callable[[], None]:
        """Register a listener; also queued as a close disposer.

        The returned cancel function still works for manual removal; unload
        removes the listener automatically (same effect mechanism as
        ``ctx.effect``, like cordis' fiber-owned listeners).
        """
        cancel = self._bus.on(event, handler)
        self._disposers.append(cancel)
        return cancel

    def emit(self, event: str, payload: Any = None) -> None:
        self._bus.emit(event, payload)

    async def emit_serial(self, event: str, payload: Any = None) -> None:
        await self._bus.emit_serial(event, payload)

    # ---- lifecycle hooks ------------------------------------------------
    def effect(self, disposer: Disposer) -> None:
        self._disposers.append(disposer)

    def on_close(self, fn: Disposer) -> None:
        self._disposers.append(fn)

    def on_start(self, fn: StartHook) -> None:
        self._start_hooks.append(fn)

    async def run_start_hooks(self) -> None:
        for hook in self._start_hooks:
            result = hook()
            if inspect.isawaitable(result):
                await result

    async def close(self) -> None:
        """Run disposers (reverse order), then drop services.

        Listener cleanup is itself a disposer (``ctx.on`` queues its cancel
        function), so no separate bus bookkeeping is needed. Each disposer is
        isolated: a failure is logged and the remaining disposers still run.
        """
        try:
            for disposer in reversed(self._disposers):
                try:
                    result = disposer()
                    if inspect.isawaitable(result):
                        await result
                # A failing disposer must not skip the rest nor the cleanup
                # below; the failure is logged (self.logger.exception) and close
                # continues.
                except Exception:
                    self.logger.exception(
                        "disposer for plugin %r failed during close", self.name
                    )
        finally:
            self._disposers.clear()
            self._services.revoke_owner(self.name)
