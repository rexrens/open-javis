"""Example plugin: register a custom tool.

Put this file (or a copy) into ~/.javis/plugins/ to enable it.

Registries are plain services: ``ctx.get(\"tools\", ToolRegistry)`` validates
the service type, and ``register`` returns a disposer handed to
``ctx.effect`` so unloading removes exactly this plugin's tool.
"""

from __future__ import annotations

from typing import Any, ClassVar

from javis.engines.corecoder.tools import ToolRegistry
from javis.engines.corecoder.tools.base import Tool


class GreetTool(Tool):
    name = "greet"
    description = "Greet someone by name"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Who to greet"},
        },
        "required": ["name"],
    }

    def execute(self, **kwargs) -> str:
        return f"Hello, {kwargs.get('name', 'world')}!"


def apply(ctx, config):
    """Register the tool. ``config`` is None unless the plugin declares a Config."""
    tools = ctx.get("tools", ToolRegistry)
    ctx.effect(tools.register(GreetTool()))
