"""Tests for PluginRegistry."""

from __future__ import annotations

import pytest

from javis.plugins.context import ServiceRegistry
from javis.plugins.instance import PluginInstance, PluginState
from javis.plugins.registry import LoadReport, PluginRegistry


def _default_ctx_builder(services):
    def build(name, config):
        from javis.plugins.context import PluginContext

        return PluginContext(
            name=name, config=config, services=services, javis_config=None
        )

    return build


@pytest.fixture
def registry():
    services = ServiceRegistry()
    return PluginRegistry(
        services=services,
        ctx_builder=_default_ctx_builder(services),
    )


def _instance(registry, name, *, inject=None, fail=False, start_timeout=0.2):
    def apply(ctx, config):
        if fail:
            raise RuntimeError("boom")

    return PluginInstance(
        name=name,
        apply_fn=apply,
        config_model=None,
        inject=inject or [],
        raw_config={},
        ctx_builder=registry.ctx_builder,
        services=registry.services,
        start_timeout=start_timeout,
    )


@pytest.mark.asyncio
async def test_activate_all_activates_ok_plugins(registry):
    registry.add(_instance(registry, "a"))
    report = await registry.activate_all()
    assert isinstance(report, LoadReport)
    assert report.loaded == ["a"]
    assert report.failed == []


@pytest.mark.asyncio
async def test_activate_all_reports_failed(registry):
    registry.add(_instance(registry, "bad", fail=True))
    report = await registry.activate_all()
    assert report.failed == ["bad"]
    assert report.loaded == []
    assert "boom" in report.errors["bad"]


@pytest.mark.asyncio
async def test_list_plugins_shows_state_and_error(registry):
    registry.add(_instance(registry, "bad", fail=True))
    await registry.activate_all()
    plugins = registry.list_plugins()
    by_name = {p["name"]: p for p in plugins}
    assert by_name["bad"]["state"] is PluginState.FAILED
    assert "boom" in str(by_name["bad"]["error"])


@pytest.mark.asyncio
async def test_close_all_disposes(registry):
    closed = []

    def apply(ctx, config):
        ctx.effect(lambda: closed.append(ctx.name) or None)

    inst = PluginInstance(
        name="c", apply_fn=apply, config_model=None, inject=[], raw_config={},
        ctx_builder=registry.ctx_builder,
        services=registry.services, start_timeout=0.2,
    )
    registry.add(inst)
    await registry.activate_all()
    await registry.close_all()
    assert closed == ["c"]
    assert inst.state is PluginState.DISPOSED


@pytest.mark.asyncio
async def test_run_start_hooks(registry):
    order = []

    def apply(ctx, config):
        ctx.on_start(lambda: order.append(ctx.name) or None)

    inst = PluginInstance(
        name="s", apply_fn=apply, config_model=None, inject=[], raw_config={},
        ctx_builder=registry.ctx_builder,
        services=registry.services, start_timeout=0.2,
    )
    registry.add(inst)
    await registry.activate_all()
    await registry.run_start_hooks()
    assert order == ["s"]
