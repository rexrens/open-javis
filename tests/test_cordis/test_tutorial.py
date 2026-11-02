"""Tutorial chapters 1-4 ported to pytest: first plugin, lifecycle/effects,
services with dependency-driven loading, and the five event dispatch modes."""

from __future__ import annotations

import asyncio
import time

import pytest

from javis.cordis import CordisError, Context, FiberState, Service


# ---------------------------------------------------------------------------
# Chapter 1 — first plugin
# ---------------------------------------------------------------------------


async def test_first_plugin_apply_called():
    ctx = Context()
    calls = []

    def apply(c):
        calls.append(c)

    fiber = ctx.plugin(apply)
    await fiber
    assert len(calls) == 1
    assert calls[0] is not None
    assert fiber.state == FiberState.ACTIVE


async def test_object_plugin():
    ctx = Context()
    seen = []

    def apply(c):
        seen.append("object-apply")

    plugin = {"name": "object-plugin", "apply": apply}
    fiber = ctx.plugin(plugin)
    await fiber
    assert seen == ["object-apply"]
    assert fiber.name == "object-plugin"


async def test_apply_error_fails_fiber():
    ctx = Context()

    def apply(c):
        raise RuntimeError("apply exploded")

    fiber = ctx.plugin(apply)
    with pytest.raises(RuntimeError):
        await fiber
    assert fiber.state == FiberState.FAILED


# ---------------------------------------------------------------------------
# Chapter 2 — lifecycle and effects
# ---------------------------------------------------------------------------


async def test_effect_disposers_reverse_order():
    ctx = Context()
    order = []

    def apply(c):
        c.effect(lambda: (order.append("e1"), lambda: order.append("d1"))[1])
        c.effect(lambda: (order.append("e2"), lambda: order.append("d2"))[1])

    fiber = ctx.plugin(apply)
    await fiber
    assert order == ["e1", "e2"]
    await fiber.dispose()
    assert order == ["e1", "e2", "d2", "d1"]


async def test_async_disposers_run_concurrently():
    ctx = Context()
    done = []

    async def disposer_a():
        await asyncio.sleep(0.03)
        done.append("a")

    async def disposer_b():
        await asyncio.sleep(0.03)
        done.append("b")

    def apply(c):
        c.effect(lambda: disposer_a)
        c.effect(lambda: disposer_b)

    fiber = ctx.plugin(apply)
    await fiber
    start = time.monotonic()
    await fiber.dispose()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"disposers ran sequentially: {elapsed:.3f}s"
    assert set(done) == {"a", "b"}


async def test_disposer_waits_for_async_cleanup():
    ctx = Context()
    cleaned = []

    async def cleanup():
        await asyncio.sleep(0.02)
        cleaned.append("done")

    def apply(c):
        c.effect(lambda: cleanup)

    fiber = ctx.plugin(apply)
    await fiber
    await fiber.dispose()
    assert cleaned == ["done"]


async def test_inactive_effect_raises():
    ctx = Context()
    fiber = ctx.plugin(lambda c: None)
    await fiber
    await fiber.dispose()
    with pytest.raises(CordisError):
        fiber.effect(lambda: None)
    with pytest.raises(CordisError):
        fiber.assertActive()


async def test_child_plugin_disposed_with_parent():
    ctx = Context()
    logs = []
    holder: dict = {}

    def child(c):
        logs.append("child-load")
        c.effect(lambda: (None, lambda: logs.append("child-cleanup"))[1])

    def parent(c):
        logs.append("parent-load")
        holder["child"] = c.plugin(child)

    fiber = ctx.plugin(parent)
    await fiber
    await holder["child"]
    await fiber.dispose()
    assert logs == ["parent-load", "child-load", "child-cleanup"]


async def test_apply_returning_disposer_is_an_effect():
    ctx = Context()
    cleaned = []

    def apply(c):
        return lambda: cleaned.append("disposed")

    fiber = ctx.plugin(apply)
    await fiber
    await fiber.dispose()
    assert cleaned == ["disposed"]


async def test_get_effects_diagnostics():
    ctx = Context()

    def apply(c):
        c.on("x", lambda: None)
        c.effect(lambda: None, "my-effect")

    fiber = ctx.plugin(apply)
    await fiber
    labels = [meta.label for meta in fiber.getEffects()]
    assert "ctx.on('x')" in labels
    assert "my-effect" in labels


# ---------------------------------------------------------------------------
# Chapter 3 — services
# ---------------------------------------------------------------------------


class Greeter:
    def __init__(self):
        self.name = "greeter"

    def greet(self, who: str) -> str:
        return f"Hello, {who}!"


async def test_provide_and_get():
    ctx = Context()

    def provider(c):
        c.provide("greeter", Greeter())

    fiber = ctx.plugin(provider)
    await fiber
    assert ctx.get("greeter").greet("world") == "Hello, world!"
    await fiber.dispose()
    assert ctx.get("greeter") is None


async def test_inject_order_independent():
    ctx = Context()
    logs = []

    def greeter(c):
        c.provide("greeter", Greeter())
        logs.append("greeter-load")

    def consumer(c):
        logs.append("consumer-load")
        assert ctx.get("greeter") is not None
        assert c.get("greeter").greet("x") == "Hello, x!"

    consumer.inject = ["greeter"]

    # Mount the consumer first; loading order must follow dependencies.
    f1 = ctx.plugin(consumer)
    f2 = ctx.plugin(greeter)
    await f1
    await f2
    assert logs == ["greeter-load", "consumer-load"]


async def test_pending_until_dependency_appears():
    ctx = Context()
    logs = []

    def needs(c):
        logs.append("loaded")

    needs.inject = ["timer"]
    fiber = ctx.plugin(needs)
    await asyncio.sleep(0.05)
    assert fiber.state == FiberState.PENDING
    assert logs == []

    def timer_provider(c):
        c.provide("timer", object())

    await ctx.plugin(timer_provider)
    await fiber
    assert fiber.state == FiberState.ACTIVE
    assert logs == ["loaded"]


async def test_dependent_unloads_when_provider_unloads():
    ctx = Context()
    events = []

    def greeter(c):
        c.provide("greeter", object())
        events.append("greeter-up")

    def consumer(c):
        events.append("consumer-up")
        c.effect(lambda: (None, lambda: events.append("consumer-down"))[1])

    consumer.inject = ["greeter"]
    f1 = ctx.plugin(consumer)
    f2 = ctx.plugin(greeter)
    await f1
    await f2
    assert events == ["greeter-up", "consumer-up"]

    await f2.dispose()
    await f1  # wait for the dependent unload to settle
    assert events == ["greeter-up", "consumer-up", "consumer-down"]

    # Re-provide: the dependent reloads with the new implementation.
    def greeter2(c):
        c.provide("greeter", object())
        events.append("greeter2-up")

    await ctx.plugin(greeter2)
    await f1
    assert events == ["greeter-up", "consumer-up", "consumer-down", "greeter2-up", "consumer-up"]


async def test_service_subclass_plugin():
    ctx = Context()

    class GreeterService(Service):
        def __init__(self, c):
            super().__init__(c, "greeter")

        def greet(self, who: str) -> str:
            return f"Hello, {who}!"

    fiber = ctx.plugin(GreeterService)
    await fiber
    assert ctx.get("greeter").greet("world") == "Hello, world!"
    assert fiber.state == FiberState.ACTIVE
    await fiber.dispose()
    assert ctx.get("greeter") is None


async def test_service_init_hook():
    ctx = Context()
    order = []

    class WithInit(Service):
        def __init__(self, c):
            super().__init__(c, "withinit")
            order.append("constructed")

        def init(self):
            order.append("init")

    await ctx.plugin(WithInit)
    assert order == ["constructed", "init"]


async def test_set_only_by_provider_fiber():
    ctx = Context()
    holder: dict = {}

    def provider(c):
        c.provide("svc", {"v": 1})
        holder["ctx"] = c

    fiber = ctx.plugin(provider)
    await fiber
    # The provider fiber may set the value...
    assert holder["ctx"].set("svc", {"v": 2}) is True
    assert ctx.get("svc")["v"] == 2
    # ...but a different context (the root here) may not.
    with pytest.raises(RuntimeError):
        ctx.set("svc", {"v": 3})
    await fiber.dispose()


async def test_ctx_inject_shorthand():
    ctx = Context()
    logs = []

    def greeter(c):
        c.provide("greeter", Greeter())
        logs.append("greeter-up")

    async def consumer(c):
        logs.append(f"consumer-{c.get('greeter').greet('dsh')}")

    await ctx.plugin(greeter)
    fiber = ctx.inject(["greeter"], consumer)
    await fiber
    assert logs == ["greeter-up", "consumer-Hello, dsh!"]


# ---------------------------------------------------------------------------
# Chapter 4 — events
# ---------------------------------------------------------------------------


async def test_emit_and_on():
    ctx = Context()
    seen = []
    disposer = ctx.on("x", lambda v: seen.append(v))
    ctx.emit("x", 1)
    ctx.emit("x", 2)
    assert seen == [1, 2]
    assert disposer() is True
    ctx.emit("x", 3)
    assert seen == [1, 2]


async def test_once():
    ctx = Context()
    seen = []
    ctx.once("x", lambda: seen.append(1))
    ctx.emit("x")
    ctx.emit("x")
    assert seen == [1]


async def test_prepend():
    ctx = Context()
    order = []
    ctx.on("x", lambda: order.append("second"))
    ctx.on("x", lambda: order.append("first"), True)  # boolean shorthand for prepend
    ctx.emit("x")
    assert order == ["first", "second"]


async def test_emit_schedules_async_listener():
    ctx = Context()
    done = []

    async def listener():
        await asyncio.sleep(0.01)
        done.append("ok")

    ctx.on("x", listener)
    ctx.emit("x")
    assert done == []
    await asyncio.sleep(0.05)
    assert done == ["ok"]


async def test_parallel():
    ctx = Context()
    order = []

    async def a():
        await asyncio.sleep(0.02)
        order.append("a")

    async def b():
        await asyncio.sleep(0.01)
        order.append("b")

    ctx.on("x", a)
    ctx.on("x", b)
    await ctx.parallel("x")
    assert order == ["b", "a"]


async def test_serial_bails_on_first_value():
    ctx = Context()
    calls = []

    def l1(v):
        calls.append(1)
        return None

    def l2(v):
        calls.append(2)
        return "ok"

    def l3(v):
        calls.append(3)

    ctx.on("x", l1)
    ctx.on("x", l2)
    ctx.on("x", l3)
    result = await ctx.serial("x", "arg")
    assert result == "ok"
    assert calls == [1, 2]


async def test_bail():
    ctx = Context()
    calls = []

    def l1():
        calls.append(1)
        return False

    def l2():
        calls.append(2)
        return 42

    ctx.on("x", l1)
    ctx.on("x", l2)
    assert ctx.bail("x") == 42
    assert calls == [1, 2]


async def test_waterfall_wrap_and_veto():
    ctx = Context()

    async def l1(input, next):
        downstream = await next()
        return downstream.upper()

    async def l2(input, next):
        if "blocked" in input:
            return "** blocked **"
        return await next()

    async def inner(input, *rest):
        return input

    ctx.on("demo/transform", l1)
    ctx.on("demo/transform", l2)
    r1 = await ctx.waterfall("demo/transform", "hello", inner)
    r2 = await ctx.waterfall("demo/transform", "blocked words", inner)
    assert r1 == "HELLO"
    assert r2 == "** BLOCKED **"


async def test_waterfall_observing_listener_must_delegate():
    ctx = Context()

    async def logger(input, next):
        return await next()  # observing listeners must call next()

    async def inner(input, *rest):
        return input.upper()

    ctx.on("demo/transform", logger)
    assert await ctx.waterfall("demo/transform", "abc", inner) == "ABC"


async def test_listener_removed_on_fiber_unload():
    ctx = Context()
    seen = []

    def plugin(c):
        c.on("x", lambda: seen.append(1))

    fiber = ctx.plugin(plugin)
    await fiber
    ctx.emit("x")
    assert seen == [1]
    await fiber.dispose()
    ctx.emit("x")
    assert seen == [1]
