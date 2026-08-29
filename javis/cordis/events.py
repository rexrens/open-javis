"""Event bus with five dispatch modes.

Port of ``vendor/cordis/src/events.ts``.

Dispatch modes::

    mode        awaited?   order                      returns?
    emit        no         registration order         no
    parallel    yes        all concurrently           no
    serial      yes        registration order         first bail value
    bail        no         registration order         first bail value
    waterfall   yes        middleware chain around next

Waterfall semantics: listeners receive ``(*args, next)``; calling ``next()``
invokes the next listener (finally the built-in behavior passed as the last
dispatch argument); *not* calling it vetoes the rest of the chain. Listeners
that only observe must delegate.

The internal ``internal/listener`` bail hook intercepts non-global
``internal/update`` registrations and scopes them to the registering fiber, so
``fiber.update()`` runs that fiber's own update hooks first (this is the
documented Cordis behavior; the vendored implementation stores them on the
root fiber instead, which would never fire them — we keep the documented
semantics).
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any, Awaitable, Callable, cast

from .fiber import is_bailed

if TYPE_CHECKING:
    from .context import Context


class EventOptions:
    __slots__ = ("prepend", "global_")

    def __init__(self, prepend: bool = False, global_: bool = False):
        self.prepend = prepend
        self.global_ = global_

    @staticmethod
    def normalize(options: Any) -> "EventOptions":
        if options is None:
            return EventOptions()
        if isinstance(options, EventOptions):
            return options
        if isinstance(options, bool):
            return EventOptions(prepend=options)
        if isinstance(options, dict):
            return EventOptions(
                prepend=bool(options.get("prepend", False)),
                global_=bool(options.get("global", False)),
            )
        return EventOptions()


class Hook:
    """Registered listener record."""

    __slots__ = ("ctx", "callback", "prepend", "global_", "once")

    def __init__(self, ctx: "Context", callback: Callable[..., Any], options: EventOptions, once: bool = False):
        self.ctx = ctx
        self.callback = callback
        self.prepend = options.prepend
        self.global_ = options.global_
        self.once = once


class EventsService:
    """Event bus installed as ``ctx.events`` and exposed via ``ctx.*`` methods."""

    def __init__(self, ctx: "Context"):
        self.ctx = ctx
        self._hooks: dict[str, list[Hook]] = {}
        # Bootstrap: intercept non-global internal/update registrations so they
        # are scoped to the registering fiber.
        self.on(self.ctx, "internal/listener", self._internal_listener)

    # -- bootstrap handlers ------------------------------------------------

    def _internal_listener(self, ctx: "Context", name: str, listener: Callable[..., Any], options: EventOptions) -> Any:
        if name == "internal/update" and not options.global_:
            fiber = ctx.fiber
            hooks = fiber._update_hooks

            def disposer() -> Any:
                if listener in hooks:
                    hooks.remove(listener)
                return None

            def _register() -> Callable[[], Any]:
                hooks.append(listener)
                return disposer

            fiber.effect(_register, 'internal/update hook')
            return disposer
        return None

    # -- dispatch -----------------------------------------------------------

    def _listeners(self, name: str, this_arg: Any | None) -> list[Hook]:
        hooks = self._hooks.get(name, [])
        if this_arg is None:
            return hooks
        filter_fn = getattr(this_arg, "filter", None)
        if filter_fn is None:
            return hooks
        return [h for h in hooks if h.global_ or filter_fn(h.ctx)]

    def _consume(self, args: tuple[Any, ...]) -> tuple[Any | None, str, list[Any]]:
        rest = list(args)
        this_arg = None
        if rest and hasattr(rest[0], "_isolate"):
            this_arg = rest.pop(0)
        name: str = rest.pop(0)
        return this_arg, name, rest

    def _fire_dispatch(self, mode: str, name: str, rest: list[Any], this_arg: Any | None) -> None:
        """Emit the ``internal/dispatch`` diagnostics event for public events.

        Mirrors Cordis: fired for non-``internal/`` events before listeners are
        delivered; ``parallel`` reports the ``emit`` mode (as in the vendored
        implementation).
        """
        if name.startswith("internal/"):
            return
        self.emit("internal/dispatch", mode, name, rest, this_arg)

    def _deliver(self, name: str, this_arg: Any | None, rest: list[Any]) -> list[Any]:
        """Build the argument list delivered to listeners.

        The ``internal/listener`` event receives the registering context as its
        first argument (the JavaScript ``this`` binding); other events receive
        only the dispatch arguments.
        """
        if name == "internal/listener" and this_arg is not None:
            return [this_arg, *rest]
        return rest

    def emit(self, *args: Any) -> None:
        """Synchronously dispatch an event; listener return values are ignored.

        Async listeners are scheduled as tasks; their errors are logged.
        """
        this_arg, name, rest = self._consume(args)
        self._fire_dispatch("emit", name, rest, this_arg)
        rest = self._deliver(name, this_arg, rest)
        for hook in self._listeners(name, this_arg):
            self._run_sync(hook, rest)

    def _run_sync(self, hook: Hook, rest: list[Any]) -> None:
        if hook.once:
            self._unregister(hook)
        try:
            result = hook.callback(*rest)
        except BaseException as error:
            self.ctx.logger.error(error)
            return
        if inspect.isawaitable(result):
            try:
                task = asyncio.ensure_future(result)
            except RuntimeError:
                self.ctx.logger.error(
                    RuntimeError("async listener scheduled outside a running event loop")
                )
                return
            task.add_done_callback(self._log_task_error)

    def _log_task_error(self, task: asyncio.Future[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.ctx.logger.error(exc)

    async def parallel(self, *args: Any) -> None:
        """Run all listeners concurrently and wait for every one to settle.

        Raises an ``ExceptionGroup`` when any listener failed (mirrors
        JavaScript's ``AggregateError``).
        """
        this_arg, name, rest = self._consume(args)
        self._fire_dispatch("emit", name, rest, this_arg)
        rest = self._deliver(name, this_arg, rest)
        hooks = self._listeners(name, this_arg)
        if not hooks:
            return
        errors: list[BaseException] = []
        pending: list[Awaitable[Any]] = []
        for hook in hooks:
            if hook.once:
                self._unregister(hook)
            try:
                result = hook.callback(*rest)
            except BaseException as error:
                errors.append(error)
                continue
            if inspect.isawaitable(result):
                pending.append(result)
        outcomes = await asyncio.gather(*pending, return_exceptions=True) if pending else []
        # BaseException-derived failures (custom BaseException subclasses)
        # must not be wrapped: Python's ExceptionGroup forbids them. Propagate
        # the first one directly instead.
        fatal = next(
            (
                o
                for o in [*errors, *outcomes]
                if isinstance(o, BaseException) and not isinstance(o, Exception)
            ),
            None,
        )
        if fatal is not None:
            raise fatal
        all_errors: list[Exception] = [
            e for e in [*errors, *outcomes] if isinstance(e, Exception)
        ]
        if all_errors:
            raise ExceptionGroup(f"parallel dispatch {name!r} failed", all_errors)

    async def serial(self, *args: Any) -> Any:
        """Await listeners in order until one returns a bail value."""
        this_arg, name, rest = self._consume(args)
        self._fire_dispatch("serial", name, rest, this_arg)
        rest = self._deliver(name, this_arg, rest)
        for hook in self._listeners(name, this_arg):
            if hook.once:
                self._unregister(hook)
            result = hook.callback(*rest)
            if inspect.isawaitable(result):
                result = await result
            if is_bailed(result):
                return result
        return None

    def bail(self, *args: Any) -> Any:
        """Call listeners synchronously until one returns a bail value."""
        this_arg, name, rest = self._consume(args)
        self._fire_dispatch("serial", name, rest, this_arg)
        rest = self._deliver(name, this_arg, rest)
        for hook in self._listeners(name, this_arg):
            if hook.once:
                self._unregister(hook)
            result = hook.callback(*rest)
            if is_bailed(result):
                return result
        return None

    def waterfall(self, *args: Any) -> Any:
        """Compose listeners around the final ``next`` callback.

        The last dispatch argument is the innermost built-in behavior; it
        receives the dispatch arguments (and the innermost ``next``), mirroring
        the JavaScript implementation.
        """
        this_arg, name, rest = self._consume(args)
        self._fire_dispatch("waterfall", name, rest, this_arg)
        rest = self._deliver(name, this_arg, rest)
        if not rest:
            raise TypeError(f"waterfall {name!r} requires an innermost next callback")
        inner = rest.pop()
        cbs: list[Hook] = list(self._listeners(name, this_arg))

        if name == "internal/update" and this_arg is not None:
            fiber = getattr(this_arg, "fiber", None)
            hooks = list(getattr(fiber, "_update_hooks", []))
            # fiber-specific hooks run first (the global head does the same
            # in Cordis)
            chain = hooks + [h.callback for h in cbs]
        else:
            chain = [h.callback for h in cbs]

        def next_fn() -> Any:
            cb = chain.pop(0) if chain else inner
            return cb(*rest)

        rest.append(next_fn)
        return next_fn()

    # -- registration -------------------------------------------------------

    def on(
        self,
        ctx: "Context",
        name: str,
        listener: Callable[..., Any],
        options: Any = None,
    ) -> Callable[[], bool]:
        """Register an event listener owned by the given context's fiber."""
        options = EventOptions.normalize(options)
        ctx.fiber.assertActive()
        # internal/listener interception may replace the registration
        result = self.bail(ctx, "internal/listener", name, listener, options)
        if result is not None:
            return cast(Callable[[], bool], result)
        hooks = self._hooks.setdefault(name, [])
        return self._register(ctx, name, hooks, listener, options)

    def once(
        self,
        ctx: "Context",
        name: str,
        listener: Callable[..., Any],
        options: Any = None,
    ) -> Callable[[], bool]:
        """Like :meth:`on`, but the listener disposes itself after its first call.

        Mirrors Cordis: the listener is wrapped in a self-disposing closure
        *before* registration, so the ``internal/listener`` interception (e.g.
        fiber-scoped ``internal/update`` hooks) stores the wrapper and `once`
        works for intercepted events too.
        """
        holder: dict[str, Any] = {}

        def wrapped(*args: Any) -> Any:
            dispose = holder.get("dispose")
            if dispose is not None:
                dispose()
            return listener(*args)

        disposer = self.on(ctx, name, wrapped, options)
        holder["dispose"] = disposer
        return disposer

    def _register(
        self,
        ctx: "Context",
        name: str,
        hooks: list[Hook],
        callback: Callable[..., Any],
        options: EventOptions,
    ) -> Callable[[], bool]:
        hook = Hook(ctx, callback, options)
        return self._register_hook(ctx, name, hooks, hook)

    def _register_hook(
        self,
        ctx: "Context",
        name: str,
        hooks: list[Hook],
        hook: Hook,
    ) -> Callable[[], bool]:
        def disposer() -> bool:
            return self._unregister(hook)

        def _register() -> Callable[[], bool]:
            if hook.prepend:
                hooks.insert(0, hook)
            else:
                hooks.append(hook)
            return disposer

        ctx.fiber.effect(_register, f"ctx.on({name!r})")
        return disposer

    def _unregister(self, hook: Hook) -> bool:
        for name, hooks in self._hooks.items():
            if hook in hooks:
                hooks.remove(hook)
                if not hooks:
                    self._hooks.pop(name, None)
                return True
        return False
