# javis/plugins/instance.py
"""Plugin instance — the lifecycle state machine (aligned with dsh's Fiber).

The state machine is the skeleton; everything else hangs off its
transitions. The plugin's own logic (inside ``apply``) is not our business:
we call it at the right time, record its state, and clean up on stop.
"""

from __future__ import annotations

import enum
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from javis.plugins.context import Disposer, PluginContext, ServiceRegistry
from javis.plugins.errors import PluginConfigError, PluginDependencyError

ApplyFn = Callable[[PluginContext, Any], Awaitable[Disposer | None] | Disposer | None]
CtxBuilder = Callable[[str, Any], PluginContext]


class PluginState(str, enum.Enum):
    PENDING = "pending"  # waiting for inject dependencies
    LOADING = "loading"  # apply(ctx, config) is running
    ACTIVE = "active"  # loaded and providing
    FAILED = "failed"  # config validation or apply threw
    UNLOADING = "unloading"  # disposers are running
    DISPOSED = "disposed"  # removed; cannot restart


class PluginInstance:
    """Runtime instance of one plugin application."""

    def __init__(
        self,
        *,
        name: str,
        apply_fn: ApplyFn,
        config_model: type[BaseModel] | None,
        inject: list[str],
        raw_config: dict[str, Any],
        ctx_builder: CtxBuilder,
        services: ServiceRegistry,
        start_timeout: float = 10.0,
    ) -> None:
        self.name = name
        self.state = PluginState.PENDING
        self.config: Any = None
        self.ctx: PluginContext | None = None
        self.error: Exception | None = None
        self._apply_fn = apply_fn
        self._config_model = config_model
        self._inject = list(inject)
        self._raw_config = raw_config
        self._ctx_builder = ctx_builder
        self._services = services
        self._start_timeout = start_timeout

    async def start(self) -> None:
        """Resolve config, wait for inject deps, run apply. Never raises."""
        if self.state is not PluginState.PENDING:
            return
        try:
            self.config = self._resolve_config()
        # BLE001: catching everything is the point — a plugin's own config code
        # must never take the process down; the failure lands in `error`/FAILED.
        except Exception as exc:  # noqa: BLE001
            err = PluginConfigError(f"plugin {self.name!r} config invalid: {exc}")
            err.__cause__ = exc  # keep the original exception, like `raise ... from`
            self._fail(err)
            return

        # Dependencies must be satisfied before the context is built: the
        # registry is a constructor parameter (no private access into ctx),
        # and building ctx registers `logger` in the shared registry — that
        # should only happen once the plugin is about to load, so a failed
        # dependency wait cannot leak an owner-less logger.
        if self._inject:
            missing = await self._services.wait_for(self._inject, self._start_timeout)
            if missing:
                self._fail(
                    PluginDependencyError(
                        f"plugin {self.name!r} missing injected services: {sorted(missing)}"
                    )
                )
                return

        self.state = PluginState.LOADING
        self.ctx = self._ctx_builder(self.name, self.config)
        ctx = self.ctx
        try:
            result = self._apply_fn(ctx, self.config)
            if inspect.isawaitable(result):
                result = await result
            if result is not None and callable(result):
                ctx.effect(result)
            self.state = PluginState.ACTIVE
        # BLE001: same rationale — apply() is arbitrary plugin code.
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    def _resolve_config(self) -> Any:
        if self._config_model is None:
            return None
        return self._config_model.model_validate(self._raw_config)

    def _fail(self, exc: Exception) -> None:
        self.error = exc
        self.state = PluginState.FAILED

    async def stop(self) -> None:
        """Run disposers (reverse order), drop listeners, revoke services."""
        if self.state is PluginState.DISPOSED:
            return
        self.state = PluginState.UNLOADING
        try:
            if self.ctx is not None:
                await self.ctx.close()
        finally:
            self.state = PluginState.DISPOSED

    @property
    def inject(self) -> list[str]:
        return list(self._inject)
