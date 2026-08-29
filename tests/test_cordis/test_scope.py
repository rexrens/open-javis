"""Isolation scopes, accessors, mixins and intercept config."""

from __future__ import annotations

import pytest

from javis.cordis import Context, FiberState


async def test_isolate_independent_service_scopes():
    ctx = Context()
    seen: dict = {}

    def provider_a(c):
        c.provide("greeter", "A")
        seen["a"] = c.fiber

    def provider_b(c):
        c.provide("greeter", "B")
        seen["b"] = c.fiber

    scope_a = ctx.isolate("greeter")
    scope_b = ctx.isolate("greeter")
    await scope_a.plugin(provider_a)
    await scope_b.plugin(provider_b)

    assert scope_a.get("greeter") == "A"
    assert scope_b.get("greeter") == "B"
    assert ctx.get("greeter") is None  # no provider at the root scope


async def test_isolate_joins_by_label():
    ctx = Context()
    label = object()
    scope1 = ctx.isolate("greeter", label)
    scope2 = ctx.isolate("greeter", label)

    def provider(c):
        c.provide("greeter", "shared")

    await scope1.plugin(provider)
    assert scope2.get("greeter") == "shared"


async def test_provide_conflict_in_same_scope():
    ctx = Context()

    def p1(c):
        c.provide("svc", 1)

    def p2(c):
        c.provide("svc", 2)

    await ctx.plugin(p1)
    fiber2 = ctx.plugin(p2)
    with pytest.raises(Exception):
        await fiber2
    assert fiber2.state == FiberState.FAILED


async def test_accessor():
    ctx = Context()
    getter_calls = []

    def apply(c):
        c.accessor("computed", lambda ctx: (getter_calls.append(1), 42)[1])

    fiber = ctx.plugin(apply)
    await fiber
    assert ctx.get("computed") == 42
    assert getter_calls == [1]
    await fiber.dispose()
    assert ctx.get("computed") is None  # accessor removed with its fiber


async def test_accessor_conflicts_with_service():
    ctx = Context()

    def apply(c):
        c.accessor("x", lambda ctx: 1)
        c.provide("x", 2)

    fiber = ctx.plugin(apply)
    with pytest.raises(Exception):
        await fiber


async def test_mixin_forwards_service_members():
    ctx = Context()

    class Greeter:
        def greet(self, who):
            return f"Hi {who}"

        value = 7

    def provider(c):
        c.provide("greeter", Greeter())
        c.mixin("greeter", ["greet", "value"])

    fiber = ctx.plugin(provider)
    await fiber
    # mixed-in members resolve through ctx.get (no proxy attribute access)
    assert ctx.get("greet")("there") == "Hi there"
    assert ctx.get("value") == 7
    await fiber.dispose()
    assert ctx.get("greet") is None


async def test_ctx_inject_with_intercept_config():
    ctx = Context()
    seen: dict = {}

    def consumer(c):
        seen["intercept"] = c._intercept.get("svc")

    def provider(c):
        c.provide("svc", object())

    await ctx.plugin(provider)

    # Object-form inject carries intercept config into the plugin context.
    fiber = ctx.plugin({"inject": {"svc": {"mode": "fast"}}, "apply": consumer})
    await fiber
    assert seen["intercept"] == {"mode": "fast"}


async def test_get_strict_gating():
    ctx = Context()
    holder: dict = {}

    def provider(c):
        c.provide("svc", "value")
        holder["fiber"] = c.fiber

    fiber = ctx.plugin(provider)
    await fiber
    assert ctx.get("svc") == "value"
    # Non-strict reads survive while the provider is unloading.
    await fiber.dispose()
    assert ctx.get("svc") is None
