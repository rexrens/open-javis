"""Fixture: registers a tool through the plugin API."""
from javis.engines.corecoder.tools.base import Tool


class PlugTool(Tool):
    name = "plug_tool"
    description = "tool registered by plugin"
    parameters = {"type": "object", "properties": {"x": {"type": "integer"}}}

    def execute(self, **kwargs) -> str:
        return str(kwargs.get("x", 0))


def apply(ctx, config):
    ctx.register_tool(PlugTool())
