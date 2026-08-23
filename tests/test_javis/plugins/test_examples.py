"""The shipped examples must load and register their extensions."""

from __future__ import annotations

from pathlib import Path

import pytest

from javis.plugins.context import EventBus, ServiceRegistry
from javis.plugins.instance import PluginState
from javis.plugins.loader import load_plugins
from javis.plugins.registry import PluginRegistry

EXAMPLES = Path(__file__).resolve().parents[3] / "examples" / "plugins"


@pytest.fixture
def reg():
    services = ServiceRegistry()
    bus = EventBus()

    def _build(name, config):
        from javis.plugins.context import PluginContext

        return PluginContext(
            name=name, config=config, services=services, bus=bus, javis_config=None
        )

    return PluginRegistry(services=services, bus=bus, ctx_builder=_build)


@pytest.mark.asyncio
async def test_example_tool_plugin_loads_and_registers(reg):
    assert EXAMPLES.is_dir(), f"examples dir missing: {EXAMPLES}"
    import javis.engines.corecoder.tools

    reg.services.provide("tools", javis.engines.corecoder.tools)  # real registry, like build_javis_runtime
    await load_plugins(reg, [EXAMPLES], {"hello_tool": {}})
    await reg.activate_all()
    from javis.engines.corecoder.tools import get_tool

    assert get_tool("greet") is not None
    tool = get_tool("greet")
    assert tool.execute(name="pi") == "Hello, pi!"
    assert reg.get("hello_tool").state is PluginState.ACTIVE


@pytest.mark.asyncio
async def test_example_command_plugin_registers_command(reg):
    from javis.commands.registry import CommandRegistry

    commands = CommandRegistry()
    reg.services.provide("commands", commands)
    await load_plugins(reg, [EXAMPLES], {"hello_command": {}})
    await reg.activate_all()
    assert commands.lookup("/hello") is not None
    assert commands.lookup("/help") is None  # 内建命令不在此 registry
