"""Every plugin shape the guide documents, plus reversibility.

The engine accepts a function, an object with ``apply``, a plain dict, a class,
and a ``Service`` subclass; a module mounted from ``cordis.yml`` is the dict
shape built from module attributes.
"""

from __future__ import annotations

import asyncio

import pytest
from javis.cordis import Context, Service
from javis.cordis.errors import ValidationError
from pydantic import BaseModel


async def test_function_plugin_receives_only_ctx_without_a_config_slot():
    ctx = Context()
    seen: list[str] = []

    def apply(ctx):
        seen.append("loaded")
        ctx.provide("greeting", "hello")

    fiber = ctx.plugin(apply)
    await fiber
    assert seen == ["loaded"]
    assert ctx.get("greeting") == "hello"


async def test_function_plugin_config_is_validated_and_passed():
    ctx = Context()

    class Config(BaseModel):
        value: int
        label: str = "n"

    def apply(ctx, config: Config):
        ctx.provide("value", config.value)
        ctx.provide("label", config.label)

    apply.Config = Config  # an attribute on the plugin object is the schema
    fiber = ctx.plugin(apply, {"value": 7})
    await fiber
    assert ctx.get("value") == 7
    assert ctx.get("label") == "n"  # defaults are applied

    broken = ctx.plugin(apply, {"value": "not-an-int"})
    with pytest.raises(ValidationError) as failure:
        await broken
    assert "value" in str(failure.value)


async def test_object_dict_and_async_plugins():
    ctx = Context()

    class Widget:
        name = "widget"
        inject = ["greeting"]

        def apply(self, ctx, config):
            ctx.provide("widget", f"{config['suffix']}-{ctx.get('greeting')}")

    ctx.plugin(lambda c: c.provide("greeting", "hi"))

    registry = {
        "name": "registry",
        "inject": ["widget"],
        "apply": lambda ctx, config: ctx.provide("registry", ctx.get("widget")),
    }

    async def async_plugin(ctx, config):
        await asyncio.sleep(0)
        ctx.provide("async", config["value"])

    async_plugin.inject = ["registry"]
    async_plugin.Config = {"x": 1}  # a plain callable also works as a schema

    fibers = [ctx.plugin(Widget(), {"suffix": "w"}), ctx.plugin(registry), ctx.plugin(async_plugin, {"value": 3})]
    await asyncio.gather(*fibers)
    assert ctx.get("widget") == "w-hi"
    assert ctx.get("registry") == "w-hi"  # inject ordered the loads
    assert ctx.get("async") == 3


async def test_class_plugin_and_service_subclass():
    ctx = Context()
    events: list[str] = []

    class Widget:
        def __init__(self, ctx, config):
            self.ctx = ctx
            self.config = config

        def init(self):
            """Instance hook run after construction; its return is an effect."""
            self.ctx.provide("widget", self.config["id"])
            return lambda: events.append("widget disposed")

    class Counter(Service):
        """``Service.__init__`` provides the instance under ``name``."""

        provide = "counter"

        def __init__(self, ctx):
            super().__init__(ctx, "counter")
            self.value = 0

        def add(self, step: int = 1) -> int:
            self.value += step
            return self.value

    widget = ctx.plugin(Widget, {"id": "w1"})
    counter = ctx.plugin(Counter)
    await widget
    await counter
    assert ctx.get("widget") == "w1"
    assert ctx.get("counter").add(2) == 2

    await widget.dispose()
    assert events == ["widget disposed"]
    assert ctx.get("widget") is None
    assert ctx.get("counter") is not None  # an unrelated fiber is untouched


async def test_dependency_driven_loading_and_unloading():
    ctx = Context()
    order: list[str] = []

    def provider(ctx):
        ctx.provide("thing", "value")
        order.append("provider")

    def consumer(ctx):
        order.append(f"consumer saw {ctx.get('thing')}")

    consumer.inject = ["thing"]
    consumer_fiber = ctx.plugin(consumer)  # mounted first, loads second
    provider_fiber = ctx.plugin(provider)
    await asyncio.gather(consumer_fiber, provider_fiber)
    assert order == ["provider", "consumer saw value"]

    # Losing the provider unloads the dependent, and restoring it reloads.
    await provider_fiber.dispose()
    assert ctx.get("thing") is None
    await settle_until(lambda: consumer_fiber.state.name == "PENDING")
    reloaded = ctx.plugin(provider)
    await reloaded
    assert order[-1] == "consumer saw value"


async def test_registration_is_reversible():
    ctx = Context()
    seen: list[str] = []

    def plugin(ctx):
        ctx.provide("service", object())
        ctx.on("ping", lambda *_: seen.append("ping"))
        ctx.effect(lambda: _setup(seen), "resource")
        return lambda: seen.append("apply returned a disposer")

    fiber = ctx.plugin(plugin)
    await fiber
    ctx.emit("ping")
    assert seen == ["setup", "ping"]

    await fiber.dispose()
    assert ctx.get("service") is None
    # Both cleanup paths ran. Their relative order is not a contract: the
    # engine rolls effect records back in reverse registration order, but a
    # record's async disposer is awaited after the synchronous ones.
    assert sorted(seen[2:]) == ["apply returned a disposer", "teardown"]
    ctx.emit("ping")
    assert "ping" not in seen[2:]  # the listener is gone


def _setup(seen: list[str]):
    """An effect body: runs immediately, returns the cleanup to run later."""
    seen.append("setup")
    return lambda: seen.append("teardown")


async def test_disposers_run_in_reverse_order_within_one_plugin():
    """Several registrations from one plugin roll back newest-first."""
    ctx = Context()
    order: list[str] = []

    def plugin(ctx):
        return [
            lambda: order.append("first registered"),
            lambda: order.append("second registered"),
            lambda: order.append("third registered"),
        ]

    fiber = ctx.plugin(plugin)
    await fiber
    await fiber.dispose()
    assert order == ["third registered", "second registered", "first registered"]


async def settle_until(predicate, timeout: float = 2.0) -> None:
    """Poll ``predicate`` until it holds (fiber transitions are asynchronous)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)
