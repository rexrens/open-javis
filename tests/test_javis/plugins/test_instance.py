"""Tests for the PluginInstance state machine."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from javis.plugins.context import PluginContext, ServiceRegistry
from javis.plugins.errors import PluginConfigError, PluginDependencyError
from javis.plugins.instance import PluginInstance, PluginState


class Cfg(BaseModel):
    greeting: str = "hi"


def make_ctx(services):
    def _build(name, config):
        return PluginContext(
            name=name, config=config, services=services, javis_config=None
        )

    return _build


@pytest.fixture
def env():
    services = ServiceRegistry()
    services.provide("tools", type("T", (), {"register": lambda self, t: (lambda: None)})())
    services.provide("commands", type("C", (), {"register": lambda self, c: (lambda: None)})())
    services.provide("engines", type("E", (), {"register": lambda self, n, f: (lambda: None)})())
    return services


@pytest.mark.asyncio
async def test_sync_apply_reaches_active(env):
    services = env
    applied = []

    def apply(ctx, config):
        applied.append(config.greeting)

    inst = PluginInstance(
        name="p",
        apply_fn=apply,
        config_model=Cfg,
        inject=[],
        raw_config={},
        ctx_builder=make_ctx(services),
        services=services,
        start_timeout=0.5,
    )
    await inst.start()
    assert inst.state is PluginState.ACTIVE
    assert applied == ["hi"]


@pytest.mark.asyncio
async def test_async_apply_supported(env):
    services = env

    async def apply(ctx, config):
        await asyncio.sleep(0.01)

    inst = PluginInstance(
        name="p",
        apply_fn=apply,
        config_model=None,
        inject=[],
        raw_config={},
        ctx_builder=make_ctx(services),
        services=services,
        start_timeout=0.5,
    )
    await inst.start()
    assert inst.state is PluginState.ACTIVE


@pytest.mark.asyncio
async def test_config_validation_failure_fails_plugin(env):
    services = env
    applied = []

    def apply(ctx, config):
        applied.append(config)

    inst = PluginInstance(
        name="p",
        apply_fn=apply,
        config_model=Cfg,
        inject=[],
        raw_config={"greeting": 123},
        ctx_builder=make_ctx(services),
        services=services,
        start_timeout=0.5,
    )
    await inst.start()
    assert inst.state is PluginState.FAILED
    assert isinstance(inst.error, PluginConfigError)
    assert applied == []  # apply never ran


@pytest.mark.asyncio
async def test_apply_exception_fails_plugin(env):
    services = env

    def apply(ctx, config):
        raise RuntimeError("boom")

    inst = PluginInstance(
        name="p",
        apply_fn=apply,
        config_model=None,
        inject=[],
        raw_config={},
        ctx_builder=make_ctx(services),
        services=services,
        start_timeout=0.5,
    )
    await inst.start()
    assert inst.state is PluginState.FAILED
    assert isinstance(inst.error, RuntimeError)


@pytest.mark.asyncio
async def test_missing_dependency_stays_pending_after_timeout(env):
    services = env

    def apply(ctx, config):
        pass

    inst = PluginInstance(
        name="p",
        apply_fn=apply,
        config_model=None,
        inject=["never-provided"],
        raw_config={},
        ctx_builder=make_ctx(services),
        services=services,
        start_timeout=0.1,
    )
    await inst.start()
    assert inst.state is PluginState.PENDING
    assert isinstance(inst.error, PluginDependencyError)
    assert "never-provided" in str(inst.error)


@pytest.mark.asyncio
async def test_dependency_provided_later_wakes_instance(env):
    services = env
    entered = []

    def apply(ctx, config):
        entered.append(True)

    inst = PluginInstance(
        name="p",
        apply_fn=apply,
        config_model=None,
        inject=["late-svc"],
        raw_config={},
        ctx_builder=make_ctx(services),
        services=services,
        start_timeout=2.0,
    )
    start_task = asyncio.create_task(inst.start())
    await asyncio.sleep(0.02)  # let it reach PENDING on the dependency
    assert inst.state is PluginState.PENDING
    services.provide("late-svc", object())
    await asyncio.wait_for(start_task, 1.0)
    assert inst.state is PluginState.ACTIVE
    assert entered == [True]


@pytest.mark.asyncio
async def test_stop_runs_disposers_in_reverse_order(env):
    services = env
    order = []

    def apply(ctx, config):
        ctx.effect(lambda: order.append("first") or None)
        ctx.effect(lambda: order.append("second") or None)

    inst = PluginInstance(
        name="p",
        apply_fn=apply,
        config_model=None,
        inject=[],
        raw_config={},
        ctx_builder=make_ctx(services),
        services=services,
        start_timeout=0.5,
    )
    await inst.start()
    await inst.stop()
    assert inst.state is PluginState.DISPOSED
    assert order == ["second", "first"]


@pytest.mark.asyncio
async def test_apply_return_value_is_used_as_disposer(env):
    services = env
    closed = []

    def apply(ctx, config):
        def disposer():
            closed.append(True)

        return disposer

    inst = PluginInstance(
        name="p",
        apply_fn=apply,
        config_model=None,
        inject=[],
        raw_config={},
        ctx_builder=make_ctx(services),
        services=services,
        start_timeout=0.5,
    )
    await inst.start()
    await inst.stop()
    assert closed == [True]


@pytest.mark.asyncio
async def test_stop_revokes_owned_service(env):
    services = env

    def apply(ctx, config):
        ctx.provide("my-svc", object())

    inst = PluginInstance(
        name="p",
        apply_fn=apply,
        config_model=None,
        inject=[],
        raw_config={},
        ctx_builder=make_ctx(services),
        services=services,
        start_timeout=0.5,
    )
    await inst.start()
    assert services.contains("my-svc")
    await inst.stop()
    assert not services.contains("my-svc")
