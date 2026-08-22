"""Example plugin: register a custom tool.

Put this file (or a copy) into ~/.javis/plugins/ to enable it.
"""

from __future__ import annotations

from corecoder.tools.base import Tool


class GreetTool(Tool):
    name = "greet"
    description = "Greet someone by name"
    parameters = {
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
    ctx.register_tool(GreetTool())
