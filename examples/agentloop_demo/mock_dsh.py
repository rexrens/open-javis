"""A tiny dsh-shaped plugin runtime for the demo.

This is intentionally a mock: it does not use the javis plugin kernel. It
provides just enough Cordis-like surface to demonstrate declarative plugin
composition, inject/provides, typed services, and a thin host. Services are
keyed by their contract type (dsh_like standard): ``ctx.services[BaseLLM]``,
``inject = [BaseLLM, ...]``, ``provides = [BaseAgentLoop]``.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

Disposer = Callable[[], Awaitable[None] | None]

log = logging.getLogger(__name__)


class DshPluginContext:
    """Per-plugin context with typed service access and effect cleanup."""

    def __init__(
        self,
        *,
        name: str,
        config: Any,
        runtime: DshRuntime,
    ) -> None:
        self.name = name
        self.config = config
        self.settings = runtime.settings
        self._runtime = runtime
        self._disposers: list[Disposer] = []
        self._start_hooks: list[Callable[[], Awaitable[None] | None]] = []

    @property
    def services(self) -> dict[Any, Any]:
        """Read access to the shared service store (dsh_like ``ctx.services``).

        Registration still goes through ``ctx.provide`` so ownership/unprovide
        bookkeeping stays intact; this property is for consumers.
        """
        return self._runtime.services

    def get(self, key: Any, value_type: type[T] | None = None) -> T:
        """Fetch a service. dsh standard: the key IS the contract type, so
        isinstance validation is implicit (``ctx.get(LLMProvider)`` checks
        the value against the key). String keys are tolerated with an
        explicit ``value_type``."""
        value = self._runtime.get(key)
        expected = value_type if value_type is not None else (key if isinstance(key, type) else None)
        if expected is not None and not isinstance(value, expected):
            raise TypeError(
                f"service {key!r} has type {type(value).__name__}, "
                f"expected {expected.__name__}"
            )
        return value

    def provide(self, key: Any, value: Any) -> Disposer:
        self._runtime.provide(key, value, owner=self.name)

        def unprovide() -> None:
            self._runtime.unprovide(key, value)

        self.effect(unprovide)
        return unprovide

    def on(self, event: str, handler: Callable[..., Any]) -> Callable[[], bool]:
        return self._runtime.on(event, handler, owner=self)

    def once(self, event: str, handler: Callable[..., Any]) -> Callable[[], bool]:
        cancel: Callable[[], bool] | None = None

        def once_handler(*args: Any, **kwargs: Any) -> Any:
            if cancel is not None:
                cancel()
            return handler(*args, **kwargs)

        cancel = self.on(event, once_handler)
        return cancel

    def emit(self, event: str, payload: Any = None) -> None:
        self._runtime.emit(event, payload)

    async def emit_serial(self, event: str, payload: Any = None) -> None:
        await self._runtime.emit_serial(event, payload)

    def effect(self, disposer: Disposer) -> None:
        self._disposers.append(disposer)

    def on_start(self, fn: Callable[[], Awaitable[None] | None]) -> None:
        self._start_hooks.append(fn)

    async def run_start_hooks(self) -> None:
        for hook in self._start_hooks:
            result = hook()
            if inspect.isawaitable(result):
                await result

    async def close(self) -> None:
        for disposer in reversed(self._disposers):
            try:
                result = disposer()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception("disposer for demo plugin %r failed", self.name)
        self._disposers.clear()


@dataclass
class PluginSpec:
    id: str
    module: str
    inject: list[Any]
    provides: list[Any]
    config: Any
    apply: Callable[[DshPluginContext, Any], Any]


class DshRuntime:
    """Thin host facade that mounts a declarative plugin composition."""

    def __init__(self, settings_path: str | Path | None = None) -> None:
        self._settings_path = Path(settings_path) if settings_path else None
        self.services: dict[Any, Any] = {}
        self._owners: dict[Any, str] = {}
        self._events: dict[str, list[tuple[DshPluginContext, Callable[..., Any]]]] = defaultdict(list)
        self._loaded: list[tuple[PluginSpec, DshPluginContext]] = []
        self.settings: dict[str, Any] = {}
        self._load_order: list[str] = []

    async def __aenter__(self) -> DshRuntime:  # noqa: PYI034
        if self._settings_path is not None:
            await self.mount_settings(self._settings_path)
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        await self.close()
        return False

    async def mount_settings(self, path: str | Path) -> None:
        settings_path = Path(path).resolve()
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        self.settings = data
        specs = [self._spec_from_entry(entry) for entry in data.get("plugins", [])]
        try:
            self._load_order = self._topological_order(specs)
            by_id = {spec.id: spec for spec in specs}
            for plugin_id in self._load_order:
                spec = by_id[plugin_id]
                ctx = DshPluginContext(name=spec.id, config=spec.config, runtime=self)
                result = spec.apply(ctx, ctx.config)
                if inspect.isawaitable(result):
                    await result
                if result is not None and callable(result):
                    ctx.effect(result)
                self._loaded.append((spec, ctx))
            for _spec, ctx in self._loaded:
                await ctx.run_start_hooks()
        except Exception:
            await self.close()
            raise

    def _spec_from_entry(self, entry: dict[str, Any]) -> PluginSpec:
        module_path = entry["module"]
        module = importlib.import_module(module_path)
        plugin_obj = getattr(module, "plugin", None)
        if isinstance(plugin_obj, dict):
            plugin_id = str(plugin_obj.get("name") or entry.get("id") or module_path.rsplit(".", 1)[-1])
            apply_fn = plugin_obj["apply"]
            config_model = plugin_obj.get("Config")
            inject = list(plugin_obj.get("inject", []))
            provides = list(plugin_obj.get("provides", []))
        else:
            plugin_id = str(getattr(module, "name", None) or entry.get("id") or module_path.rsplit(".", 1)[-1])
            apply_fn = getattr(module, "apply", None)
            if apply_fn is None:
                raise TypeError(f"plugin module {module_path!r} has no apply")
            config_model = getattr(module, "Config", None)
            inject = list(getattr(module, "inject", []))
            provides = list(getattr(module, "provides", []))

        raw_config = entry.get("config", {})
        if config_model is not None:
            config = config_model.model_validate(raw_config)
        else:
            config = raw_config
        return PluginSpec(
            id=plugin_id,
            module=module_path,
            inject=inject,
            provides=provides,
            config=config,
            apply=apply_fn,
        )

    def _topological_order(self, specs: list[PluginSpec]) -> list[str]:
        provider_by_service: dict[Any, Any] = {}
        for spec in specs:
            for service in spec.provides:
                if service in provider_by_service:
                    raise RuntimeError(
                        f"duplicate provider for demo service {service!r}: "
                        f"{provider_by_service[service]!r} and {spec.id!r}"
                    )
                provider_by_service[service] = spec.id

        graph: dict[str, list[str]] = {spec.id: [] for spec in specs}
        in_degree: dict[str, int] = {spec.id: 0 for spec in specs}
        for spec in specs:
            for service in spec.inject:
                provider = provider_by_service.get(service)
                if provider is None or provider == spec.id:
                    continue
                graph[provider].append(spec.id)
                in_degree[spec.id] += 1

        ready = [plugin_id for plugin_id, degree in in_degree.items() if degree == 0]
        order: list[str] = []
        while ready:
            plugin_id = ready.pop(0)
            order.append(plugin_id)
            for consumer in graph[plugin_id]:
                in_degree[consumer] -= 1
                if in_degree[consumer] == 0:
                    ready.append(consumer)
        if len(order) != len(specs):
            missing = [plugin_id for plugin_id in in_degree if in_degree[plugin_id] > 0]
            raise RuntimeError(f"cyclic demo plugin dependency: {sorted(missing)}")
        return order

    def get(self, key: Any, value_type: type[T] | None = None) -> T:
        if key not in self.services:
            raise KeyError(f"demo service {key!r} is not provided")
        value = self.services[key]
        expected = value_type if value_type is not None else (key if isinstance(key, type) else None)
        if expected is not None and not isinstance(value, expected):
            raise TypeError(
                f"service {key!r} has type {type(value).__name__}, "
                f"expected {expected.__name__}"
            )
        return value

    def provide(self, key: Any, value: Any, owner: str | None = None) -> None:
        self.services[key] = value
        if owner is not None:
            self._owners[key] = owner

    def unprovide(self, key: Any, value: Any) -> None:
        if self.services.get(key) is value:
            self.services.pop(key, None)
            self._owners.pop(key, None)

    def on(self, event: str, handler: Callable[..., Any], owner: DshPluginContext | None = None) -> Callable[[], bool]:
        handlers = self._events[event]
        handlers.append((owner, handler))

        def cancel() -> bool:
            try:
                handlers.remove((owner, handler))
            except ValueError:
                return False
            return True

        if owner is not None:
            owner.effect(cancel)
        return cancel

    def emit(self, event: str, payload: Any = None) -> None:
        for _owner, handler in list(self._events.get(event, [])):
            try:
                result = handler(payload)
            except Exception:
                log.exception("demo event handler for %r failed", event)
                continue
            if inspect.isawaitable(result):
                asyncio.get_running_loop().create_task(_consume(result, event))

    async def emit_serial(self, event: str, payload: Any = None) -> None:
        for _owner, handler in list(self._events.get(event, [])):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

    async def close(self) -> None:
        for _spec, ctx in reversed(self._loaded):
            await ctx.close()
        self._loaded.clear()
        self._events.clear()
        self.services.clear()
        self._owners.clear()


async def _consume(awaitable: Awaitable[None], event: str) -> None:
    try:
        await awaitable
    except Exception:
        log.exception("async demo event handler for %r failed", event)


__all__ = ["DshPluginContext", "DshRuntime", "PluginSpec"]
