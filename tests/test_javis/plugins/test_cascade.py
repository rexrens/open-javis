"""Tests for PluginRegistry dependency graph, cascade unload and load order."""

from __future__ import annotations

import pytest

from javis.plugins.context import ServiceRegistry
from javis.plugins.instance import PluginInstance, PluginState
from javis.plugins.registry import PluginRegistry


def make_registry() -> PluginRegistry:
    services = ServiceRegistry()

    def build(name, config):
        from javis.plugins.context import PluginContext

        return PluginContext(name=name, config=config, services=services, javis_config=None)

    return PluginRegistry(services=services, ctx_builder=build)


def add_plugin(
    reg: PluginRegistry,
    name: str,
    *,
    inject: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
    dispose_log: list[str] | None = None,
) -> PluginInstance:
    def apply(ctx, config):
        for svc in provides:
            ctx.provide(svc, object())
        if dispose_log is not None:
            ctx.effect(lambda: dispose_log.append(name))

    inst = PluginInstance(
        name=name,
        apply_fn=apply,
        config_model=None,
        inject=list(inject),
        raw_config={},
        ctx_builder=reg.ctx_builder,
        services=reg.services,
        start_timeout=0.5,
    )
    reg.add(inst)
    return inst


@pytest.mark.asyncio
async def test_dependency_graph_from_runtime_provides_and_inject():
    reg = make_registry()
    add_plugin(reg, "a", provides=("svc-a",))
    add_plugin(reg, "b", inject=("svc-a",))
    add_plugin(reg, "c")  # independent leaf
    reg.services.provide("builtin", object())  # owner=None → no edge
    add_plugin(reg, "d", inject=("builtin",))
    await reg.activate_all()

    graph = reg.dependency_graph()
    assert graph["a"] == ["b"]
    assert graph["b"] == []
    assert graph["c"] == []
    # built-in services (no owner) never create dependency edges
    assert graph["d"] == []


@pytest.mark.asyncio
async def test_load_order_providers_before_dependents():
    reg = make_registry()
    add_plugin(reg, "a", provides=("s1",))
    add_plugin(reg, "b", inject=("s1",))
    add_plugin(reg, "c")  # independent
    await reg.activate_all()
    # deterministic: zero in-degree in registration order, then their dependents
    assert reg.load_order() == ["a", "c", "b"]


@pytest.mark.asyncio
async def test_unload_cascades_transitively_dependents_first():
    reg = make_registry()
    order: list[str] = []
    add_plugin(reg, "a", provides=("s1",), dispose_log=order)
    add_plugin(reg, "b", inject=("s1",), provides=("s2",), dispose_log=order)
    add_plugin(reg, "c", inject=("s2",), dispose_log=order)
    add_plugin(reg, "d")  # independent — must survive
    await reg.activate_all()
    assert reg.get("a").state is PluginState.ACTIVE
    assert reg.get("d").state is PluginState.ACTIVE

    stopped = await reg.unload("a")
    assert stopped == ["c", "b", "a"]  # dependents first, provider last
    assert order == ["c", "b", "a"]
    for name in ("a", "b", "c"):
        assert reg.get(name).state is PluginState.DISPOSED
    assert reg.get("d").state is PluginState.ACTIVE  # untouched


@pytest.mark.asyncio
async def test_unload_unknown_and_already_disposed_are_noop():
    reg = make_registry()
    add_plugin(reg, "a")
    await reg.activate_all()
    assert await reg.unload("nope") == []
    assert await reg.unload("a") == ["a"]
    assert await reg.unload("a") == []  # already disposed


@pytest.mark.asyncio
async def test_unload_revokes_provider_services():
    reg = make_registry()
    add_plugin(reg, "a", provides=("s1",))
    add_plugin(reg, "b", inject=("s1",))
    await reg.activate_all()
    assert reg.services.contains("s1")

    await reg.unload("a")
    assert not reg.services.contains("s1")  # owner-revoked with the plugin


@pytest.mark.asyncio
async def test_close_all_stops_dependents_before_providers():
    reg = make_registry()
    order: list[str] = []
    add_plugin(reg, "a", provides=("s1",), dispose_log=order)
    add_plugin(reg, "b", inject=("s1",), dispose_log=order)
    await reg.activate_all()

    await reg.close_all()
    assert order == ["b", "a"]  # reverse topological
    for name in ("a", "b"):
        assert reg.get(name).state is PluginState.DISPOSED


@pytest.mark.asyncio
async def test_cyclic_dependency_does_not_crash_shutdown():
    """A provide/inject cycle must not hang or raise close_all (fallback order)."""
    reg = make_registry()
    add_plugin(reg, "a", provides=("s1",), inject=("s2",))
    add_plugin(reg, "b", provides=("s2",), inject=("s1",))
    await reg.activate_all()  # both stay pending waiting on each other
    assert {p["state"] for p in reg.list_plugins()} == {PluginState.PENDING}

    order = reg.load_order()  # cycle → acyclic prefix + registration-order remainder
    assert len(order) == 2
    await reg.close_all()
    for name in ("a", "b"):
        assert reg.get(name).state is PluginState.DISPOSED
