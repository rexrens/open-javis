"""Tests for PluginContext, ServiceRegistry and EventBus."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest
from pydantic import BaseModel

from javis.engines.corecoder.tools.base import Tool
from javis.plugins.context import PluginContext, ServiceRegistry


class CtxTool(Tool):
    name = "ctx_test_tool"
    description = "tool registered through ctx"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        return "ok"


class PlainService:
    def __init__(self, x: int) -> None:
        self.x = x


class SvcModel(BaseModel):
    value: int


class FakeTools:
    """Minimal stand-in for a typed tools-registry service (register + get)."""

    def __init__(self) -> None:
        self._tools: dict = {}

    def register(self, tool):
        self._tools[tool.name] = tool
        return lambda: self._tools.pop(tool.name, None)

    def get(self, name):
        return self._tools.get(name)


class FakeCommands:
    """Minimal stand-in for CommandRegistry."""

    def __init__(self) -> None:
        self._cmds: dict = {}

    def register(self, command):
        self._cmds[command.name] = command
        return lambda: self._cmds.pop(command.name, None)


class FakeEngines:
    """Minimal stand-in for EngineRegistry."""

    def __init__(self) -> None:
        self._engines: dict = {}

    def register(self, name, factory):
        self._engines[name] = factory
        return lambda: self._engines.pop(name, None)


@pytest.fixture
def ctx():
    services = ServiceRegistry()
    services.provide("tools", FakeTools())
    services.provide("commands", FakeCommands())
    services.provide("engines", FakeEngines())
    # bus omitted: PluginContext creates its own internal EventBus
    return PluginContext(name="p1", config=None, services=services, javis_config=None)


def test_provide_and_get(ctx):
    ctx.provide("svc", 42)
    assert ctx.get("svc") == 42


def test_get_with_pydantic_model_validates():
    services = ServiceRegistry()
    services.provide("cfg", {"value": 3})
    cfg = services.get("cfg", SvcModel)
    assert isinstance(cfg, SvcModel)
    assert cfg.value == 3


def test_get_with_pydantic_model_mismatch_raises():
    services = ServiceRegistry()
    services.provide("cfg", {"value": "nope"})
    with pytest.raises(Exception, match="validation error"):
        services.get("cfg", SvcModel)


def test_get_with_plain_type_checks_instance():
    services = ServiceRegistry()
    svc = PlainService(1)
    services.provide("svc", svc)
    assert services.get("svc", PlainService) is svc


def test_get_with_mismatched_type_raises():
    services = ServiceRegistry()
    services.provide("svc", 42)
    with pytest.raises(TypeError, match="expected"):
        services.get("svc", str)


def test_get_typed_missing_returns_none():
    services = ServiceRegistry()
    assert services.get("missing", PlainService) is None


def test_ctx_get_with_type_validates(ctx):
    ctx.provide("cfg", {"value": 1})
    cfg = ctx.get("cfg", SvcModel)
    assert isinstance(cfg, SvcModel)
    assert cfg.value == 1


def test_get_unknown_service_raises(ctx):
    with pytest.raises(KeyError):
        ctx.get("nope")


def test_plugin_reaches_registry_service_with_type_check(ctx):
    tools = ctx.get("tools", FakeTools)
    assert isinstance(tools, FakeTools)
    tools.register(CtxTool())
    assert tools.get("ctx_test_tool") is not None


def test_typed_get_mismatch_raises(ctx):
    with pytest.raises(TypeError, match="expected"):
        ctx.get("tools", FakeCommands)


def test_register_disposer_unregisters(ctx):
    tools = ctx.get("tools", FakeTools)
    cancel = tools.register(CtxTool())
    assert tools.get("ctx_test_tool") is not None
    cancel()
    assert tools.get("ctx_test_tool") is None


def test_on_returns_manual_cancel(ctx):
    seen = []
    cancel = ctx.on("evt", lambda payload: seen.append(payload))
    ctx.emit("evt", "a")
    assert seen == ["a"]
    cancel()
    ctx.emit("evt", "b")
    assert seen == ["a"]  # cancelled listener no longer receives events


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
    # listener cleanup is a disposer: the event key is dropped entirely
    assert "evt" not in ctx._bus._listeners


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
    assert "evt" not in ctx._bus._listeners  # listener disposer ran too


@pytest.mark.asyncio
async def test_parallel_waits_for_all_handlers(ctx):
    seen: list[str] = []

    async def first(_payload: object) -> None:
        await asyncio.sleep(0.01)
        seen.append("first")

    async def second(_payload: object) -> None:
        seen.append("second")

    ctx.on("evt", first)
    ctx.on("evt", second)
    await ctx.parallel("evt", "x")
    assert set(seen) == {"first", "second"}


@pytest.mark.asyncio
async def test_parallel_reports_handler_failures(ctx):
    async def boom(_payload: object) -> None:
        raise RuntimeError("boom")

    ctx.on("evt", boom)
    with pytest.raises(RuntimeError, match="boom"):
        await ctx.parallel("evt")


@pytest.mark.asyncio
async def test_serial_bails_after_first_non_false_value(ctx):
    reached: list[str] = []

    def first(_payload: object) -> str | None:
        return None

    def second(_payload: object) -> str:
        return "stop"

    def third(_payload: object) -> None:
        reached.append("third")

    ctx.on("evt", first)
    ctx.on("evt", second)
    ctx.on("evt", third)
    assert await ctx.serial("evt", "x") == "stop"
    assert reached == []


def test_bail_stops_synchronously(ctx):
    reached: list[str] = []

    def first(_payload: object) -> bool:
        return False

    def second(_payload: object) -> str:
        return "stop"

    def third(_payload: object) -> None:
        reached.append("third")

    ctx.on("evt", first)
    ctx.on("evt", second)
    ctx.on("evt", third)
    assert ctx.bail("evt", "x") == "stop"
    assert reached == []


@pytest.mark.asyncio
async def test_waterfall_wraps_downstream_result(ctx):
    async def downstream(value: str) -> str:
        return value.upper()

    async def handler(payload: str, next: object) -> str:
        result = await next(payload)
        return result + "!"

    ctx.on("evt", handler)
    assert await ctx.waterfall("evt", "hi", downstream) == "HI!"


@pytest.mark.asyncio
async def test_waterfall_can_short_circuit(ctx):
    async def downstream(_value: str) -> str:
        return "unreachable"

    ctx.on("evt", lambda payload, next: "blocked")
    assert await ctx.waterfall("evt", "go", downstream) == "blocked"


def test_once_removes_listener_after_first_call(ctx):
    calls: list[str] = []
    ctx.once("evt", lambda payload: calls.append(payload))
    ctx.emit("evt", "a")
    ctx.emit("evt", "b")
    assert calls == ["a"]


def test_prepend_controls_listener_order(ctx):
    order: list[str] = []
    ctx.on("evt", lambda _payload: order.append("second"))
    ctx.on("evt", lambda _payload: order.append("first"), prepend=True)
    ctx.emit("evt")
    assert order == ["first", "second"]
