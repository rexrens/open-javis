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
EventHandler = Callable[..., Any]
StartHook = Callable[[], Awaitable[None] | None]
ServiceChangeListener = Callable[[str, bool, str | None], None]

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
        self._listeners: list[ServiceChangeListener] = []

    def provide(
        self,
        name: str,
        value: Any,
        owner: str | None = None,
        *,
        publish: bool = True,
    ) -> None:
        self._services[name] = value
        if owner is not None:
            self._owners[name] = owner
        try:
            asyncio.get_running_loop().create_task(self._notify())
        except RuntimeError:
            pass  # no running loop — nothing to wake
        if publish:
            self._emit_change(name, True, owner)

    def add_listener(self, listener: ServiceChangeListener) -> Callable[[], None]:
        """Subscribe to service provide/revoke notifications."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def publish_owner(self, owner: str | None) -> None:
        """Publish all services owned by ``owner`` to dependency listeners."""
        for name, service_owner in list(self._owners.items()):
            if service_owner == owner:
                self._emit_change(name, True, owner)

    def _emit_change(self, name: str, provided: bool, owner: str | None) -> None:
        for listener in list(self._listeners):
            try:
                listener(name, provided, owner)
            except Exception:
                logging.getLogger("javis.plugins").exception(
                    "service change listener failed for %r", name
                )

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
        removed: list[tuple[str, str | None]] = []
        for name, service_owner in list(self._owners.items()):
            if service_owner == owner:
                removed.append((name, service_owner))
                del self._services[name]
                del self._owners[name]
        for name, removed_owner in removed:
            self._emit_change(name, False, removed_owner)


class EventBus:
    """Cross-plugin event dispatch with Cordis-style modes.

    Internal implementation detail: listener cleanup is delegated to the
    disposers ``PluginContext.on`` registers, so this class carries no owner
    concept.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[EventHandler]] = {}

    def on(
        self,
        event: str,
        handler: EventHandler,
        *,
        prepend: bool = False,
    ) -> Callable[[], None]:
        handlers = self._listeners.setdefault(event, [])
        if prepend:
            handlers.insert(0, handler)
        else:
            handlers.append(handler)

        def cancel() -> None:
            handlers = self._listeners.get(event)
            if not handlers:
                return
            if handler in handlers:
                handlers.remove(handler)
            if not handlers:
                del self._listeners[event]

        return cancel

    def once(
        self,
        event: str,
        handler: EventHandler,
        *,
        prepend: bool = False,
    ) -> Callable[[], None]:
        cancel: Callable[[], None] | None = None

        def once_handler(payload: Any) -> Any:
            if cancel is not None:
                cancel()
            return handler(payload)

        cancel = self.on(event, once_handler, prepend=prepend)
        return cancel

    def emit(self, event: str, payload: Any = None) -> None:
        for handler in list(self._listeners.get(event, ())):
            result = handler(payload)
            if inspect.isawaitable(result):
                asyncio.get_running_loop().create_task(_consume(result, event))

    async def emit_serial(self, event: str, payload: Any = None) -> None:
        for handler in list(self._listeners.get(event, ())):
            await _call_handler(handler, payload)

    async def parallel(self, event: str, payload: Any = None) -> None:
        handlers = list(self._listeners.get(event, ()))
        if not handlers:
            return
        results = await asyncio.gather(
            *(_call_handler(handler, payload) for handler in handlers),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            raise RuntimeError(
                f"{len(errors)} parallel handler(s) failed for event {event!r}: "
                + "; ".join(str(error) for error in errors)
            )

    async def serial(self, event: str, payload: Any = None) -> Any:
        for handler in list(self._listeners.get(event, ())):
            result = await _call_handler(handler, payload)
            if is_bailed(result):
                return result
        return None

    def bail(self, event: str, payload: Any = None) -> Any:
        for handler in list(self._listeners.get(event, ())):
            result = handler(payload)
            if is_bailed(result):
                return result
        return None

    async def waterfall(self, event: str, payload: Any, next: Callable[[Any], Any]) -> Any:
        handlers = list(self._listeners.get(event, ()))

        async def run(index: int, value: Any) -> Any:
            if index >= len(handlers):
                result = next(value)
                return await _maybe_await(result)
            handler = handlers[index]

            def continue_chain(next_value: Any) -> Any:
                return run(index + 1, next_value)

            result = handler(value, continue_chain)
            return await _maybe_await(result)

        return await run(0, payload)


async def _consume(awaitable: Awaitable[None], event: str) -> None:
    try:
        await awaitable
    except Exception:  # fire-and-forget handlers must not crash the loop
        logging.getLogger("javis.plugins").exception(
            "event handler for %r failed", event
        )


def is_bailed(value: Any) -> bool:
    """Cordis bail semantics: non-None/non-False values stop serial dispatch."""
    return value is not None and value is not False


async def _call_handler(handler: EventHandler, payload: Any) -> Any:
    result = handler(payload)
    return await _maybe_await(result)


async def _maybe_await(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


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
        self.provided_services: list[str] = []

    # ---- services -------------------------------------------------------
    def provide(self, name: str, value: Any) -> None:
        self._services.provide(name, value, owner=self.name, publish=False)
        self.provided_services.append(name)

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

    def __getattr__(self, name: str) -> Any:
        """Attribute access to services: ``ctx.tools`` ≡ ``ctx.get("tools")``.

        Only called when normal attribute lookup fails, so real attributes
        (``name`` / ``config`` / ``logger`` / …) are never shadowed.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self.get(name)
        except KeyError:
            raise AttributeError(name) from None

    # ---- events ---------------------------------------------------------
    def on(
        self,
        event: str,
        handler: EventHandler,
        *,
        prepend: bool = False,
    ) -> Callable[[], None]:
        """Register a listener; also queued as a close disposer.

        The returned cancel function still works for manual removal; unload
        removes the listener automatically (same effect mechanism as
        ``ctx.effect``, like cordis' fiber-owned listeners).
        """
        cancel = self._bus.on(event, handler, prepend=prepend)
        self._disposers.append(cancel)
        return cancel

    def once(
        self,
        event: str,
        handler: EventHandler,
        *,
        prepend: bool = False,
    ) -> Callable[[], None]:
        cancel = self._bus.once(event, handler, prepend=prepend)
        self._disposers.append(cancel)
        return cancel

    def emit(self, event: str, payload: Any = None) -> None:
        self._bus.emit(event, payload)

    async def emit_serial(self, event: str, payload: Any = None) -> None:
        await self._bus.emit_serial(event, payload)

    async def parallel(self, event: str, payload: Any = None) -> None:
        await self._bus.parallel(event, payload)

    async def serial(self, event: str, payload: Any = None) -> Any:
        return await self._bus.serial(event, payload)

    def bail(self, event: str, payload: Any = None) -> Any:
        return self._bus.bail(event, payload)

    async def waterfall(self, event: str, payload: Any, next: Callable[[Any], Any]) -> Any:
        return await self._bus.waterfall(event, payload, next)

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
