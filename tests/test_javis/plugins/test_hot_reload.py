"""Configuration hot update and plugin HMR tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from javis.plugins.context import PluginContext, ServiceRegistry
from javis.plugins.errors import PluginConfigError
from javis.plugins.hot_reload import PluginWatcher
from javis.plugins.instance import PluginInstance, PluginState
from javis.plugins.loader import load_plugins, reload_plugin
from javis.plugins.registry import PluginRegistry


def make_registry() -> PluginRegistry:
    services = ServiceRegistry()

    def build(name: str, config: object) -> PluginContext:
        return PluginContext(name=name, config=config, services=services)

    return PluginRegistry(services=services, ctx_builder=build)


class Config(BaseModel):
    value: int = 1


def add_config_plugin(reg: PluginRegistry, values: list[int]) -> PluginInstance:
    def apply(ctx: PluginContext, config: Config) -> None:
        values.append(config.value)
        ctx.provide("svc", config.value)

    instance = PluginInstance(
        name="p",
        apply_fn=apply,
        config_model=Config,
        inject=[],
        raw_config={"value": 1},
        ctx_builder=reg.ctx_builder,
        services=reg.services,
    )
    reg.add(instance)
    return instance


@pytest.mark.asyncio
async def test_update_validates_and_restarts_plugin():
    reg = make_registry()
    values: list[int] = []
    instance = add_config_plugin(reg, values)
    await reg.activate_all()
    assert values == [1]

    await reg.update("p", {"value": 2})
    assert values == [1, 2]
    assert instance.config.value == 2
    assert instance.state is PluginState.ACTIVE


@pytest.mark.asyncio
async def test_invalid_update_keeps_old_config():
    reg = make_registry()
    values: list[int] = []
    instance = add_config_plugin(reg, values)
    await reg.activate_all()

    with pytest.raises(PluginConfigError):
        await reg.update("p", {"value": "not-an-int"})

    assert values == [1]
    assert instance.config.value == 1
    assert instance.state is PluginState.ACTIVE


@pytest.mark.asyncio
async def test_update_many_only_returns_changed_plugins():
    reg = make_registry()
    values: list[int] = []
    add_config_plugin(reg, values)
    await reg.activate_all()

    changed = await reg.update_many({"p": {"config": {"value": 3}}})
    assert changed == ["p"]
    assert values == [1, 3]

    changed = await reg.update_many({"p": {"config": {"value": 3}}})
    assert changed == []


@pytest.mark.asyncio
async def test_reload_plugin_reimports_module(tmp_path: Path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    plugin_file = plugin_dir / "reloadable.py"
    plugin_file.write_text(
        "value = 1\n"
        "name = 'reloadable'\n"
        "inject = []\n"
        "provides = ['svc']\n"
        "def apply(ctx, config):\n"
        "    ctx.provide('svc', value)\n",
        encoding="utf-8",
    )

    reg = make_registry()
    await load_plugins(reg, [plugin_dir], {})
    await reg.activate_all()
    assert reg.services.get("svc") == 1

    plugin_file.write_text(
        "value = 2\n"
        "name = 'reloadable'\n"
        "inject = []\n"
        "provides = ['svc']\n"
        "def apply(ctx, config):\n"
        "    ctx.provide('svc', value)\n",
        encoding="utf-8",
    )
    await reload_plugin(reg, [plugin_dir], {}, "reloadable")
    assert reg.services.get("svc") == 2


@pytest.mark.asyncio
async def test_watcher_reloads_plugin_config_from_file(tmp_path: Path):
    reg = make_registry()
    values: list[int] = []
    add_config_plugin(reg, values)
    await reg.activate_all()

    config_file = tmp_path / "config.json"
    config_file.write_text(
        '{"plugins": {"p": {"enabled": true, "config": {"value": 5}}}}',
        encoding="utf-8",
    )
    watcher = PluginWatcher(registry=reg, dirs=[tmp_path], plugins_cfg={}, config_path=config_file)
    await watcher._reload_config(config_file)
    assert values == [1, 5]
    assert reg.get("p").config.value == 5
