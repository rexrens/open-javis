"""Runtime dependency tracking: service provide/revoke stops and starts fibers."""

from __future__ import annotations

import pytest

from javis.plugins.context import PluginContext, ServiceRegistry
from javis.plugins.instance import PluginInstance, PluginState
from javis.plugins.registry import PluginRegistry


def make_registry() -> PluginRegistry:
    services = ServiceRegistry()

    def build(name: str, config: object) -> PluginContext:
        return PluginContext(name=name, config=config, services=services)

    return PluginRegistry(services=services, ctx_builder=build)


def add_plugin(
    reg: PluginRegistry,
    name: str,
    *,
    inject: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
    dispose_log: list[str] | None = None,
) -> PluginInstance:
    def apply(ctx: PluginContext, config: object) -> object:
        for service in provides:
            ctx.provide(service, object())
        if dispose_log is not None:
            ctx.effect(lambda: dispose_log.append(name))
        return None

    instance = PluginInstance(
        name=name,
        apply_fn=apply,
        config_model=None,
        inject=list(inject),
        raw_config={},
        ctx_builder=reg.ctx_builder,
        services=reg.services,
        start_timeout=0.1,
    )
    reg.add(instance)
    return instance


@pytest.mark.asyncio
async def test_provided_service_starts_pending_dependent():
    reg = make_registry()
    consumer = add_plugin(reg, "consumer", inject=("svc",))

    await reg.activate_all()
    assert consumer.state is PluginState.PENDING

    reg.services.provide("svc", object(), owner=None)
    await reg.settle()
    assert consumer.state is PluginState.ACTIVE


@pytest.mark.asyncio
async def test_service_revoke_stops_transitive_dependents():
    reg = make_registry()
    provider = add_plugin(reg, "provider", provides=("s1",))
    middle = add_plugin(reg, "middle", inject=("s1",), provides=("s2",))
    leaf = add_plugin(reg, "leaf", inject=("s2",))
    independent = add_plugin(reg, "independent")

    await reg.activate_all()
    assert all(inst.state is PluginState.ACTIVE for inst in reg._instances.values())

    await reg.unload("provider")
    assert leaf.state is PluginState.DISPOSED
    assert middle.state is PluginState.DISPOSED
    assert provider.state is PluginState.DISPOSED
    assert independent.state is PluginState.ACTIVE


@pytest.mark.asyncio
async def test_reprovided_service_restarts_dependents():
    reg = make_registry()
    provider = add_plugin(reg, "provider", provides=("svc",))
    consumer = add_plugin(reg, "consumer", inject=("svc",))

    await reg.activate_all()
    await reg.unload("provider")
    assert consumer.state is PluginState.DISPOSED

    await provider.restart()
    await reg.settle()
    assert provider.state is PluginState.ACTIVE
    assert consumer.state is PluginState.ACTIVE
