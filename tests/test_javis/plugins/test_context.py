"""Tests for PluginContext, ServiceRegistry and EventBus."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from javis.engines.corecoder.tools.base import Tool
from javis.plugins.context import EventBus, PluginContext, ServiceRegistry


class CtxTool(Tool):
    name = "ctx_test_tool"
    description = "tool registered through ctx"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        return "ok"


@pytest.fixture
def ctx():
    services = ServiceRegistry()
    bus = EventBus()
    services.provide("tools", type("T", (), {
        "register_tool": lambda self, t: None,
        "get_tool": lambda self, n: "found" if n == "ctx_test_tool" else None,
    })())
    services.provide("commands", type("C", (), {"register": lambda self, c: None})())
    services.provide("engines", type("E", (), {"register_engine": lambda self, n, f: None})())
    return PluginContext(name="p1", config=None, services=services, bus=bus, javis_config=None)


def test_provide_and_get(ctx):
    ctx.provide("svc", 42)
    assert ctx.get("svc") == 42


def test_get_unknown_service_raises(ctx):
    with pytest.raises(KeyError):
        ctx.get("nope")


def test_register_tool_goes_to_tools_service(ctx):
    ctx.register_tool(CtxTool())
    tools = ctx.get("tools")
    assert tools.get_tool("ctx_test_tool") is not None


def test_on_emit_sync_handler(ctx):
    seen = []
    ctx.on("evt", lambda payload: seen.append(payload))
    ctx.emit("evt", "x")
    assert seen == ["x"]


@pytest.mark.asyncio
async def test_emit_serial_awaits_async_handler(ctx):
    done = []

    async def handler(payload):
        await asyncio.sleep(0.01)
        done.append(payload)

    ctx.on("evt", handler)
    await ctx.emit_serial("evt", "y")
    assert done == ["y"]


def test_effect_disposers_run_in_reverse_order(ctx):
    order = []
    ctx.effect(lambda: order.append("first") or None)
    ctx.effect(lambda: order.append("second") or None)

    async def _close():
        await ctx.close()

    asyncio.run(_close())
    assert order == ["second", "first"]


def test_close_revokes_services_and_listeners(ctx):
    ctx.provide("svc", 1)
    ctx.on("evt", lambda p: None)

    async def _close():
        await ctx.close()

    asyncio.run(_close())
    assert not ctx._services.contains("svc")
    assert ctx._bus._listeners.get("evt", {}) == {}  # owner listeners removed


def test_close_continues_after_disposer_failure(ctx):
    order = []

    def boom():
        raise RuntimeError("bad disposer")

    ctx.effect(lambda: order.append("first") or None)
    ctx.effect(boom)
    ctx.effect(lambda: order.append("third") or None)
    ctx.provide("svc", 1)
    ctx.on("evt", lambda p: None)

    async def _close():
        await ctx.close()

    asyncio.run(_close())
    # Reverse order; "boom" raises but the remaining disposers still run.
    assert order == ["third", "first"]
    # finally-block cleanup still ran despite the failure.
    assert not ctx._services.contains("svc")
    assert ctx._bus._listeners.get("evt", {}) == {}
