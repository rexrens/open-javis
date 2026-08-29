"""Fiber: the runtime instance of one plugin application.

Port of ``vendor/cordis/src/fiber.ts``. A fiber tracks dependency state (the
*epoch* mechanism), validated config, lifecycle effects, and cleanup for the
context returned by ``ctx.plugin()``.

State machine::

    PENDING -> LOADING -> ACTIVE -> UNLOADING -> DISPOSED
                 \\-> FAILED

- PENDING  — declared, but required services are not yet available.
- LOADING  — the plugin body is running.
- ACTIVE   — loaded and providing.
- FAILED   — the plugin body or its config threw.
- UNLOADING / DISPOSED — disposers running / everything torn down.
"""

from __future__ import annotations

import asyncio
import inspect
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .errors import CordisError, ValidationError
from .scope import InterceptMap

if TYPE_CHECKING:
    from .context import Context
    from .registry import Runtime

INACTIVE = "__INACTIVE__"


class FiberState(IntEnum):
    PENDING = 0
    LOADING = 1
    ACTIVE = 2
    FAILED = 3
    DISPOSED = 4
    UNLOADING = 5


class EffectMeta:
    """Tree node exposing nested effect labels for diagnostics."""

    __slots__ = ("label", "children")

    def __init__(self, label: str, children: list["EffectMeta"] | None = None):
        self.label = label
        self.children = children if children is not None else []

    def __repr__(self) -> str:
        return f"EffectMeta({self.label!r}, children={self.children!r})"


class EffectRecord:
    """One registered effect: its label, collected disposers and diagnostics."""

    __slots__ = ("label", "disposers", "active", "meta", "disposer")

    def __init__(self, label: str):
        self.label = label
        self.disposers: list[Callable[[], Any]] = []
        self.active = True
        self.meta = EffectMeta(label)
        self.disposer: Callable[[], Any] | None = None


def is_bailed(value: Any) -> bool:
    """Return whether an event result should stop a bail-style dispatch."""
    return value is not None and value is not False


def _is_basemodel(value: Any) -> bool:
    try:
        from pydantic import BaseModel
    except ImportError:  # pragma: no cover
        return False
    return inspect.isclass(value) and issubclass(value, BaseModel)


def resolve_config(runtime: Any, config: Any) -> Any:
    """Validate and normalize config against a runtime's ``Config`` schema.

    ``runtime.Config`` may be a pydantic ``BaseModel`` subclass (validated via
    ``model_validate``), a plain callable (a transform), or ``None`` (no
    validation).
    """
    if runtime is None or runtime.Config is None:
        return config
    cfg = runtime.Config
    if _is_basemodel(cfg):
        try:
            return cfg.model_validate(config)
        except Exception as error:
            issues = []
            for err in getattr(error, "errors", lambda: [])():
                loc = err.get("loc") or ()
                msg = err.get("msg") or err.get("type") or str(err)
                issues.append({"msg": msg, "loc": loc})
            raise ValidationError(issues) from error
    if callable(cfg):
        return cfg(config)
    return config


def _config_call_style(callback: Any) -> str:
    """How should a plugin body be invoked?

    Mirrors JavaScript, where the plugin is always called with ``(ctx, config)``
    and un-declared arguments are ignored: Python signatures that declare a
    config slot (positional with or without a default, or keyword-only) receive
    the config; a single-parameter body receives only ``ctx``.

    Returns ``'both'`` (pass ``(ctx, config)``), ``'kwonly'`` (pass
    ``config=config``), ``'ctx'`` (pass only ``ctx``), or ``'none'``.
    """
    try:
        sig = inspect.signature(callback)
    except (TypeError, ValueError):  # builtins / C callables
        return "both"
    params = list(sig.parameters.values())
    if not params:
        return "none"
    var_pos = any(p.kind is p.VAR_POSITIONAL for p in params)
    var_kw = any(p.kind is p.VAR_KEYWORD for p in params)
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if var_pos or len(positional) >= 2:
        return "both"
    if any(p.kind is p.KEYWORD_ONLY for p in params) or var_kw:
        return "kwonly"
    return "ctx"


def _call_with_config(callback: Callable[..., Any], ctx: Any, config: Any) -> Any:
    """Call a plugin body with ``(ctx, config)`` when it declares a config
    parameter (with or without a default, or keyword-only); otherwise with
    ``ctx`` alone."""
    style = _config_call_style(callback)
    if style == "both":
        return callback(ctx, config)
    if style == "kwonly":
        return callback(ctx, config=config)
    if style == "none":
        return callback()
    return callback(ctx)


def _construct_plugin(cls: type, ctx: Any, config: Any) -> Any:
    """Construct a class plugin with ``(ctx, config)`` unless the constructor
    only accepts ``ctx`` (JavaScript ignores extra arguments; Python does not).
    """
    style = _config_call_style(cls)
    if style == "both":
        return cls(ctx, config)
    if style == "kwonly":
        return cls(ctx, config=config)
    if style == "none":
        return cls()
    return cls(ctx)


class Fiber:
    """Runtime instance of one plugin application."""

    def __init__(
        self,
        parent: "Context",
        config: Any,
        inject: dict[str, Any],
        runtime: "Runtime | None",
        get_outer_stack: Callable[[], list[str]] | None = None,
    ):
        self.parent = parent
        self.inject = inject  # service name -> intercept config (or None)
        self.runtime = runtime
        self._config = config
        self.config: Any = None
        self.store: dict[str, Any] | None = None  # required-service snapshot while loaded
        self.inertia: asyncio.Future[Any] | None = None
        self._error: BaseException | None = None
        self._store: dict[str, Any] = {}  # required-service impl snapshot
        self._store_provided: dict[str, Any] = {}  # services this fiber provides
        self._update_hooks: list[Callable[..., Any]] = []  # non-global internal/update hooks
        self._effects: list[EffectRecord] = []  # registered effects, in order
        self._epoch = INACTIVE
        self._get_outer_stack = get_outer_stack

        if runtime is not None:
            self.uid: int | None = parent.registry.counter
            self.ctx = self.context = parent.extend({"fiber": self})
            # merge inject intercept config over inherited ancestor entries
            intercept = InterceptMap(parent._intercept)
            for name, cfg in inject.items():
                if cfg is not None:
                    intercept.set(name, cfg)
            self.context._intercept = intercept
            self.state = FiberState.PENDING

            # Registering this fiber is itself an effect of the parent fiber:
            # disposing the parent disposes every child it mounted.
            self.dispose = parent.fiber.effect(self._dispose_body, "ctx.plugin()")

            # Publish the fiber so observers (e.g. the loader) may extend its
            # `inject` map before dependencies are evaluated.
            try:
                self.ctx.events.emit("internal/plugin", self)
            except BaseException:
                async def _rollback() -> None:
                    try:
                        await self.dispose()
                    except BaseException:
                        pass

                asyncio.ensure_future(_rollback())
                raise

            if self.uid is not None and parent.fiber.state != FiberState.UNLOADING:
                for name in self.inject:
                    self._check_impl(name)
                self._refresh()
        else:
            # Root fiber: owns the root context, always ACTIVE.
            self.uid = 0
            self.ctx = self.context = parent
            self.state = FiberState.ACTIVE
            self.store = {}
            self._epoch = ""
            self.dispose = self.restart

    # -- diagnostics --------------------------------------------------------

    @property
    def name(self) -> str:
        """Display name, inherited from the nearest named ancestor."""
        fiber: Fiber = self
        while True:
            if fiber.runtime is not None and fiber.runtime.name:
                return fiber.runtime.name
            if fiber.parent is None or fiber.parent.fiber is fiber:
                return "root"
            fiber = fiber.parent.fiber

    def assertActive(self) -> None:
        """Raise ``CordisError('INACTIVE_EFFECT')`` if the fiber is disposed."""
        if self.uid is None:
            raise CordisError("INACTIVE_EFFECT")

    # -- effects ------------------------------------------------------------

    def effect(
        self,
        execute: Callable[[], Any],
        label: str = "anonymous",
    ) -> Callable[[], Awaitable[None]]:
        """Register a cleanup-aware effect on this fiber.

        ``execute`` runs immediately; disposers it produces are collected and
        run (in reverse order, sequentially) when the returned disposer is
        called or the fiber unloads, whichever comes first. Calling the
        disposer twice is a no-op.
        """
        self.assertActive()
        if self.state == FiberState.UNLOADING:
            raise CordisError("INACTIVE_EFFECT")

        record = EffectRecord(label)
        self._effects.append(record)

        try:
            result = execute()
        except BaseException:
            record.active = False
            raise

        if inspect.isawaitable(result):

            async def _setup() -> None:
                try:
                    value = await result
                except BaseException as error:
                    self.ctx.logger.error(error)
                    return
                self._collect_effect(record, value)

            setup_task = asyncio.ensure_future(_setup())

            def _log_setup_error(task: asyncio.Future[Any]) -> None:
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None:
                    self.ctx.logger.error(exc)

            # Ensure an async execute that never settles on a live fiber still
            # reports its failure (the disposer also retrieves it on unload).
            setup_task.add_done_callback(_log_setup_error)
        else:
            self._collect_effect(record, result)
            setup_task = None

        async def disposer() -> None:
            if not record.active:
                return
            record.active = False
            if setup_task is not None:
                try:
                    await setup_task
                except BaseException:
                    pass
            await _run_record_disposers(self, record)

        # The fiber's unload invokes each effect's disposer (not the raw
        # disposer list), so an async setup is awaited before cleanup runs.
        record.disposer = disposer

        # Expose the effect's diagnostic tree so a parent effect collecting
        # this disposer can attach it as a child (Cordis `symbols.effect`).
        disposer.__cordis_effect_meta__ = record.meta  # type: ignore[attr-defined]
        return disposer

    def _collect_effect(self, record: EffectRecord, result: Any) -> None:
        """Normalize an effect body result into collected disposers.

        Accepted shapes (mirroring Cordis ``Effect``): a single disposer, an
        awaitable of one, or a (possibly async) iterable yielding several.
        """
        if result is None:
            return
        if callable(result):
            record.disposers.append(result)
            nested = getattr(result, "__cordis_effect_meta__", None)
            if nested is not None:
                record.meta.children.append(nested)
            return
        if inspect.isawaitable(result):
            task = asyncio.ensure_future(result)

            def _done(fut: asyncio.Future[Any]) -> None:
                if fut.cancelled():
                    return
                exc = fut.exception()
                if exc is not None:
                    self.ctx.logger.error(exc)
                    return
                self._collect_effect(record, fut.result())

            task.add_done_callback(_done)
            return
        if hasattr(result, "__aiter__"):

            async def _iterate() -> None:
                async for item in result:
                    self._collect_item(record, item)

            asyncio.ensure_future(_iterate())
            return
        if hasattr(result, "__iter__"):
            for item in result:
                self._collect_item(record, item)
            return
        raise TypeError("Invalid effect")

    def _collect_item(self, record: EffectRecord, item: Any) -> None:
        if item is None:
            # JS `safeCollect`: nullable items in an effect iterable are
            # skipped, so tuples like `(None, disposer)` are valid.
            return
        if callable(item):
            record.disposers.append(item)
            nested = getattr(item, "__cordis_effect_meta__", None)
            if nested is not None:
                record.meta.children.append(nested)
        elif inspect.isawaitable(item):
            self._collect_effect(record, item)
        else:
            raise TypeError("Invalid effect")

    def getEffects(self) -> list[EffectMeta]:
        """Return metadata for currently registered effects."""
        return [record.meta for record in self._effects if record.active]

    # -- dependency machinery ----------------------------------------------

    def _check_impl(self, name: str) -> None:
        impl = self.ctx.reflect._get_impl(self.ctx, name, strict=True)
        if impl is None:
            self._store.pop(name, None)
            return
        try:
            if impl.check is not None and not impl.check(impl.value):
                self._store.pop(name, None)
                return
        except BaseException as error:
            impl.fiber.ctx.logger.error(error)
            self._store.pop(name, None)
            return
        self._store[name] = impl

    def _refresh(self) -> None:
        """Recompute the dependency epoch and drive a transition if needed."""
        epoch: str = ""
        for name in self.inject:
            impl = self._store.get(name)
            if impl is None:
                epoch = INACTIVE
                break
            epoch += ":" + str(impl.fiber.uid)
        self._set_epoch(epoch)

    def _set_epoch(self, epoch: str) -> None:
        old_epoch = self._epoch
        if epoch == old_epoch:
            return
        self._epoch = epoch
        if self.inertia is not None:
            # An in-flight transition will re-check the epoch when it settles.
            return
        self._update_state(lambda: self._start_transition(old_epoch))

    def _start_transition(self, old_epoch: str) -> FiberState:
        if self._epoch != INACTIVE and old_epoch == INACTIVE:
            self.inertia = asyncio.ensure_future(self._reload())
            return FiberState.LOADING
        self.inertia = asyncio.ensure_future(self._unload())
        return FiberState.UNLOADING

    def _update_state(self, callback: Callable[[], FiberState | None] | None = None) -> None:
        old_state = self.state
        if callback is not None:
            result = callback()
            self.state = result if result is not None else self._get_state()
        else:
            self.state = self._get_state()
        if old_state == self.state:
            return
        self.ctx.events.emit("internal/status", self, old_state)
        # Only notify between ACTIVE and non-ACTIVE states.
        if old_state != FiberState.ACTIVE and self.state != FiberState.ACTIVE:
            return
        for name in list(self._store_provided):
            self.ctx.reflect.notify(self.ctx, [name])

    def _get_state(self) -> FiberState:
        if self.uid is None:
            return FiberState.DISPOSED
        if self._error is not None:
            return FiberState.FAILED
        if self._epoch != INACTIVE:
            return FiberState.ACTIVE
        return FiberState.PENDING

    # -- lifecycle ----------------------------------------------------------

    def _dispose_body(self) -> Callable[[], Any]:
        runtime = self.runtime
        assert runtime is not None  # only registered for non-root fibers
        runtime.fibers.append(self)

        def remove() -> None:
            if self in runtime.fibers:
                runtime.fibers.remove(self)

        async def disposer() -> None:
            self.uid = None
            self.ctx.events.emit("internal/plugin", self)
            if self.ctx.registry._runtimes.get(runtime.callback) is runtime:
                remove()
                if not runtime.fibers:
                    self.ctx.registry._runtimes.pop(runtime.callback, None)
            self._set_epoch(INACTIVE)
            # A PENDING fiber may already own effects registered by an
            # `internal/plugin` observer; its epoch is still INACTIVE so
            # `_set_epoch` has no transition to drive — unload explicitly.
            if self.inertia is None:
                self._update_state(self._force_unload)
            while self.inertia is not None:
                try:
                    await self.inertia
                except BaseException:
                    break

        return disposer

    def _force_unload(self) -> FiberState:
        self.inertia = asyncio.ensure_future(self._unload())
        return FiberState.UNLOADING

    def _resolve_config(self, config: Any) -> Any:
        config = self.ctx.events.waterfall(
            self.ctx, "internal/config", config, lambda *args: config
        )
        return resolve_config(self.runtime, config) if self.runtime is not None else config

    async def _reload(self) -> None:
        self.store = dict(self._store)
        old_epoch = self._epoch
        try:
            # A disposer queued before this point may already have invalidated
            # the load; do not run plugin code for a stale epoch.
            if self._epoch == old_epoch:
                self.config = self._resolve_config(self._config)
                await self._run_body()
                self._error = None
        except BaseException as reason:
            self.ctx.logger.error(reason)
            self._error = reason
            self._epoch = INACTIVE
        self._update_state(self._reload_finalize(old_epoch))

    def _reload_finalize(self, old_epoch: str) -> Callable[[], FiberState | None]:
        def finalize() -> FiberState | None:
            if self._epoch == old_epoch:
                self.inertia = None
                return None
            self.inertia = asyncio.ensure_future(self._unload())
            return FiberState.UNLOADING

        return finalize

    async def _run_body(self) -> None:
        """Run the plugin body and collect its effect result (if any).

        The root fiber (``runtime is None``) has a no-op body so
        ``restart()``/``dispose()`` on the root context is safe (mirrors the
        JavaScript root fiber's dedicated no-op runner).
        """
        if self.runtime is None:
            return
        callback = self.runtime.callback
        record = EffectRecord("apply")
        self._effects.append(record)
        if inspect.isclass(callback):
            instance = _construct_plugin(callback, self.ctx, self.config)
            init = getattr(instance, "init", None)
            result = init() if init is not None else None
        else:
            result = _call_with_config(callback, self.ctx, self.config)
        if inspect.isawaitable(result):
            result = await result
        self._collect_effect(record, result)

    async def _unload(self) -> None:
        # 1. Unregister provided services so dependents unload first.
        provided = list(self._store_provided)
        for name in provided:
            label = self.ctx._isolate.get(name)
            self.ctx.reflect.store.pop(label, None)
            self._store_provided.pop(name, None)
        if provided:
            self.ctx.reflect.notify(self.ctx, provided)

        # 2. Run effect disposers: reverse registration order; async ones run
        #    concurrently; errors are logged, never propagated. Each record's
        #    own disposer is invoked (not the raw list) so an async effect
        #    setup is awaited before its cleanup runs (no leaked disposers).
        records = self._effects
        self._effects = []
        results = []
        for record in reversed(records):
            if record.disposer is not None:
                result = _call_disposer(record.disposer)
                if result is not None:
                    results.append(result)
                continue
            record.active = False
            for disposer in reversed(record.disposers):
                result = _call_disposer(disposer)
                if result is not None:
                    results.append(result)
        if results:
            outcomes = await asyncio.gather(*results, return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    self.ctx.logger.error(outcome)

        self.store = None
        self._update_state(self._unload_finalize)

    def _unload_finalize(self) -> FiberState | None:
        if self._epoch == INACTIVE:
            self.inertia = None
            return None
        self.inertia = asyncio.ensure_future(self._reload())
        return FiberState.LOADING

    # -- public lifecycle API ----------------------------------------------

    async def await_(self) -> "Fiber":
        """Wait for current lifecycle work and rethrow startup errors."""
        while self.inertia is not None:
            task = self.inertia
            try:
                await task
            except BaseException:
                pass
            # A failed transition must not leave an infinite wait behind.
            if self.inertia is task and task.done():
                self.inertia = None
                break
        if self._error is not None:
            raise self._error
        return self

    def __await__(self) -> Any:
        return self.await_().__await__()

    async def restart(self) -> None:
        """Dispose and immediately reload this plugin with its current config."""
        self.assertActive()
        self._set_epoch(INACTIVE)
        self._refresh()
        await self.await_()

    def update(self, config: Any, noSave: bool = False) -> Any:
        """Validate and apply new config, then restart the plugin.

        Runs the ``internal/update`` waterfall first so update hooks can veto
        or replace the restart. The raw config is committed to ``_config`` only
        after validation succeeds, so a failed update never poisons a later
        ``restart()`` or HMR reload.
        """
        self.assertActive()
        if self.state != FiberState.ACTIVE:
            # Config resolution may access injected services; defer it until
            # the fiber can activate.
            self._config = config
            self._error = None
            self._set_epoch(INACTIVE)
            self._refresh()
            return None
        resolved = self._resolve_config(config)  # may raise ValidationError
        self._config = config

        def default(*args: Any) -> Any:
            self.config = resolved
            self._error = None
            return self.restart()

        return self.ctx.events.waterfall(self.ctx, "internal/update", resolved, noSave, default)


def _call_disposer(disposer: Callable[[], Any]) -> Any:
    try:
        result = disposer()
        if inspect.isawaitable(result):
            return asyncio.ensure_future(result)
        return None
    except BaseException as error:
        return _failed_future(error)


def _failed_future(error: BaseException) -> asyncio.Future[Any]:
    fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
    fut.set_exception(error)
    return fut


async def _run_record_disposers(fiber: Fiber, record: EffectRecord) -> None:
    """Run one effect's disposers in reverse order, chained sequentially."""
    for disposer in reversed(record.disposers):
        result = _call_disposer(disposer)
        if result is not None:
            try:
                await result
            except BaseException as error:
                fiber.ctx.logger.error(error)
