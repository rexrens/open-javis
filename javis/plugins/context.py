"""Plugin context — service registry, cross-plugin events, lifecycle hooks.

- ``ServiceRegistry``: shared across plugins; ``provide`` wakes waiting
  instances (asyncio.Condition); owners let unload revoke exactly its services.
- ``EventBus``: cross-plugin broadcast; listeners grouped by owner plugin so
  unload removes exactly that plugin's listeners.
- ``PluginContext``: the ``ctx`` handed to ``apply(ctx, config)``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

Disposer = Callable[[], Awaitable[None] | None]
EventHandler = Callable[[Any], Awaitable[None] | None]
StartHook = Callable[[], Awaitable[None] | None]


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

    def get(self, name: str) -> Any:
        return self._services.get(name)

    def contains(self, name: str) -> bool:
        return name in self._services

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
    """Cross-plugin event dispatch (emit = fire-and-forget, emit_serial = await)."""

    def __init__(self) -> None:
        # event name -> owner plugin -> [handlers]
        self._listeners: dict[str, dict[str, list[EventHandler]]] = {}

    def on(self, event: str, owner: str, handler: EventHandler) -> Callable[[], None]:
        self._listeners.setdefault(event, {}).setdefault(owner, []).append(handler)

        def cancel() -> None:
            handlers = self._listeners.get(event, {}).get(owner)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return cancel

    def emit(self, event: str, payload: Any = None) -> None:
        for owner, handlers in list(self._listeners.get(event, {}).items()):
            for handler in list(handlers):
                result = handler(payload)
                if inspect.isawaitable(result):
                    asyncio.get_running_loop().create_task(_consume(result, event, owner))

    async def emit_serial(self, event: str, payload: Any = None) -> None:
        for owner, handlers in list(self._listeners.get(event, {}).items()):
            for handler in list(handlers):
                result = handler(payload)
                if inspect.isawaitable(result):
                    await result

    def remove_owner(self, owner: str) -> None:
        for event in self._listeners:
            self._listeners[event].pop(owner, None)


async def _consume(awaitable: Awaitable[None], event: str, owner: str) -> None:
    try:
        await awaitable
    except Exception:  # fire-and-forget handlers must not crash the loop
        logging.getLogger("javis.plugins").exception(
            "event handler for %r of plugin %r failed", event, owner
        )


class PluginContext:
    """Per-plugin context handed to ``apply(ctx, config)``."""

    def __init__(
        self,
        *,
        name: str,
        config: Any,
        services: ServiceRegistry,
        bus: EventBus,
        javis_config: Any,
    ) -> None:
        self.name = name
        self.config = config
        self.javis_config = javis_config
        self.logger = logging.getLogger(f"javis.plugins.{name}")
        self._services = services
        self._bus = bus
        self._disposers: list[Disposer] = []
        self._start_hooks: list[StartHook] = []
        self._registered_tools: list[str] = []

    # ---- services -------------------------------------------------------
    def provide(self, name: str, value: Any) -> None:
        self._services.provide(name, value, owner=self.name)

    def get(self, name: str) -> Any:
        value = self._services.get(name)
        if value is None and not self._services.contains(name):
            raise KeyError(f"Service {name!r} not provided")
        return value

    # ---- extension points ----------------------------------------------
    def register_tool(self, tool: Any) -> None:
        self._services.get("tools").register_tool(tool)
        self._registered_tools.append(tool.name)

    def register_command(self, command: Any) -> None:
        self._services.get("commands").register(command)

    def register_engine(self, name: str, factory: Any) -> None:
        self._services.get("engines").register_engine(name, factory)

    # ---- events ---------------------------------------------------------
    def on(self, event: str, handler: EventHandler) -> Callable[[], None]:
        return self._bus.on(event, self.name, handler)

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
        """Run disposers (reverse order), then drop listeners, tools and services.

        Each disposer is isolated: a failure is logged and the remaining
        disposers still run. Listener/service/tool cleanup lives in the
        ``finally`` so it always runs even when a disposer raises.
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
            self._bus.remove_owner(self.name)
            self._services.revoke_owner(self.name)
            for name in self._registered_tools:
                self._services.get("tools").unregister_tool(name)
            self._registered_tools.clear()
