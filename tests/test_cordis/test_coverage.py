"""Coverage for implemented-but-untested behavior (table B) and the recently
fixed gaps: internal events, getEffects children, Service symbol methods."""

from __future__ import annotations

import asyncio

import pytest

from javis.cordis import (
    Context,
    CordisError,
    FiberState,
    Service,
)


# ---------------------------------------------------------------------------
# B-table: implemented but untested behavior
# ---------------------------------------------------------------------------


async def test_parallel_raises_exception_group():
    ctx = Context()

    async def good():
        pass

    async def bad():
        raise ValueError("boom")

    ctx.on("x", good)
    ctx.on("x", bad)
    with pytest.raises(ExceptionGroup) as excinfo:
        await ctx.parallel("x")
    assert any(isinstance(e, ValueError) for e in excinfo.value.exceptions)


async def test_listener_global_bypasses_context_filter():
    ctx = Context()
    calls = []
    ctx.on("x", lambda: calls.append("normal"))
    ctx.on("x", lambda: calls.append("global"), {"global": True})

    # A context whose filter rejects every hook.
    filter_ctx = ctx.extend()
    filter_ctx.filter = lambda hook_ctx: False

    ctx.events.emit(filter_ctx, "x")
    assert calls == ["global"]


async def test_listener_filtered_out_without_global():
    ctx = Context()
    calls = []
    ctx.on("x", lambda: calls.append("nope"))
    filter_ctx = ctx.extend()
    filter_ctx.filter = lambda hook_ctx: False
    ctx.events.emit(filter_ctx, "x")
    assert calls == []


async def test_once_with_prepend():
    ctx = Context()
    order = []
    ctx.on("x", lambda: order.append("second"))
    ctx.once("x", lambda: order.append("first"), True)  # boolean shorthand for prepend
    ctx.emit("x")
    ctx.emit("x")
    assert order == ["first", "second"]


async def test_effect_with_async_execute():
    ctx = Context()
    cleaned = []

    async def execute():
        await asyncio.sleep(0.01)
        return lambda: cleaned.append("done")

    def apply(c):
        c.effect(execute, "async-effect")

    fiber = ctx.plugin(apply)
    await fiber
    assert cleaned == []
    await asyncio.sleep(0.05)  # async setup collects the disposer
    await fiber.dispose()
    assert cleaned == ["done"]


async def test_effect_disposer_double_call_is_noop():
    ctx = Context()
    cleaned = []

    def apply(c):
        return lambda: cleaned.append("x")

    fiber = ctx.plugin(apply)
    await fiber
    await fiber.dispose()
    await fiber.dispose()  # second call must be a no-op
    assert cleaned == ["x"]


async def test_registry_delete_disposes_fibers():
    ctx = Context()
    cleaned = []

    def apply(c):
        c.effect(lambda: (None, lambda: cleaned.append("down"))[1])

    f1 = ctx.plugin(apply)
    f2 = ctx.plugin(apply)
    await f1
    await f2
    assert ctx.registry.has(apply)
    assert ctx.registry.delete(apply) is not None
    assert not ctx.registry.has(apply)
    await asyncio.sleep(0.05)  # disposal is fire-and-forget
    assert cleaned == ["down", "down"]


async def test_extend_meta_shadows_without_mutating_parent():
    ctx = Context()
    child = ctx.extend({"answer": 42})
    seen: dict = {}

    def apply(c):
        seen["answer"] = c.answer

    await child.plugin(apply)
    assert seen["answer"] == 42
    assert not hasattr(ctx, "answer")


async def test_class_plugin_init_returns_effect():
    ctx = Context()
    cleaned = []

    class P(Service):
        def __init__(self, c):
            super().__init__(c, "p")

        def init(self):
            return lambda: cleaned.append("init-disposed")

    fiber = ctx.plugin(P)
    await fiber
    await fiber.dispose()
    assert cleaned == ["init-disposed"]


async def test_cycle_dependency_stays_pending():
    ctx = Context()

    def a_provider(c):
        c.provide("a", 1)

    def b_provider(c):
        c.provide("b", 2)

    a_provider.inject = ["b"]
    b_provider.inject = ["a"]
    f1 = ctx.plugin(a_provider)
    f2 = ctx.plugin(b_provider)
    await asyncio.sleep(0.05)
    assert f1.state == FiberState.PENDING
    assert f2.state == FiberState.PENDING
    assert ctx.get("a") is None
    assert ctx.get("b") is None


async def test_serial_awaits_async_listeners():
    ctx = Context()

    async def l1(v):
        await asyncio.sleep(0.01)
        return None

    async def l2(v):
        return "async-ok"

    ctx.on("x", l1)
    ctx.on("x", l2)
    assert await ctx.serial("x", "arg") == "async-ok"


async def test_cordis_error_code_and_message():
    ctx = Context()
    fiber = ctx.plugin(lambda c: None)
    await fiber
    await fiber.dispose()
    with pytest.raises(CordisError) as excinfo:
        fiber.effect(lambda: None)
    assert excinfo.value.code == "INACTIVE_EFFECT"
    assert str(excinfo.value) == "cannot create effect on inactive context"


async def test_context_is_context():
    ctx = Context()
    assert Context.is_context(ctx)
    assert Context.is_context(ctx.extend())
    assert not Context.is_context(object())


async def test_fiber_name_inheritance():
    ctx = Context()
    holder: dict = {}

    def parent(c):
        holder["child"] = c.plugin(lambda c: None)

    parent.name = "named-parent"
    fiber = ctx.plugin(parent)
    await fiber
    await holder["child"]
    assert holder["child"].name == "named-parent"
    assert fiber.name == "named-parent"


# ---------------------------------------------------------------------------
# Fixed gaps: internal events, getEffects children, Service symbol methods
# ---------------------------------------------------------------------------


async def test_internal_service_event_on_provide_and_unprovide():
    ctx = Context()
    events = []
    ctx.on("internal/service", lambda name, value: events.append((name, value)))

    def provider(c):
        c.provide("svc", "val")

    fiber = ctx.plugin(provider)
    await fiber
    assert ("svc", "val") in events

    await fiber.dispose()
    assert ("svc", None) in events  # unprovide notifies with value None


async def test_internal_service_event_scoped_to_isolate():
    ctx = Context()
    events = []
    ctx.on("internal/service", lambda name, value: events.append((name, value)))

    def provider(c):
        c.provide("greeter", "isolated")

    scope = ctx.isolate("greeter")
    await scope.plugin(provider)
    # The root-scope listener is filtered out: the event is scoped to the
    # isolated label.
    assert all(name != "greeter" for name, _ in events)


async def test_internal_dispatch_event_fired_for_public_events_only():
    ctx = Context()
    seen = []
    ctx.on("internal/dispatch", lambda mode, name, args, this_arg: seen.append((mode, name)))
    ctx.on("pub/event", lambda: None)
    ctx.emit("pub/event", 1)
    ctx.emit("internal/status", None, None)  # internal events must not fire it
    assert ("emit", "pub/event") in seen
    assert not any(name == "internal/status" for _, name in seen)


async def test_get_effects_children_for_nested_effects():
    ctx = Context()

    def apply(c):
        def outer():
            inner = c.effect(lambda: (None, lambda: None), "inner-effect")
            return inner

        c.effect(outer, "outer-effect")

    fiber = ctx.plugin(apply)
    await fiber
    metas = {meta.label: meta for meta in fiber.getEffects()}
    assert "outer-effect" in metas
    child_labels = [child.label for child in metas["outer-effect"].children]
    assert "inner-effect" in child_labels


async def test_service_resolve_config_merges_ancestors_base_head():
    ctx = Context()
    holder: dict = {}

    class Svc(Service):
        def __init__(self, c):
            super().__init__(c, "svc")
            holder["svc"] = self

    intercept_ctx = ctx.intercept("svc", {"root-level": 1})
    await intercept_ctx.plugin(Svc)
    merged = holder["svc"].resolve_config({"base": 0}, {"head": 9})
    assert merged == {"base": 0, "root-level": 1, "head": 9}


async def test_service_resolve_config_shadowing_and_own_entries():
    ctx = Context()
    holder: dict = {}

    class Svc(Service):
        def __init__(self, c):
            super().__init__(c, "svc")
            holder["svc"] = self

    intercept_ctx = ctx.intercept("svc", {"root-level": 1})
    deeper = intercept_ctx.intercept("svc", {"deeper": 2})
    await deeper.plugin(Svc)
    # Both ancestor and nearer entries survive (chain, not flat dict).
    assert holder["svc"].resolve_config() == {"root-level": 1, "deeper": 2}


async def test_service_filter_helper():
    ctx = Context()

    class Svc(Service):
        def __init__(self, c):
            super().__init__(c, "svc")

    await ctx.plugin(Svc)
    svc = ctx.get("svc")
    assert svc.filter(ctx) is True
    isolated = ctx.isolate("svc")
    assert svc.filter(isolated) is False


async def test_service_extend_service():
    ctx = Context()

    class Svc(Service):
        def __init__(self, c):
            super().__init__(c, "svc")

        def greet(self):
            return f"hello {self.extra}" if hasattr(self, "extra") else "hello"

    await ctx.plugin(Svc)
    svc = ctx.get("svc")
    derived = svc.extend_service(extra="dsh")
    assert derived.greet() == "hello dsh"
    assert derived.name == "svc"
    assert derived.ctx is svc.ctx
