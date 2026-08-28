"""Tool registry.

``ToolRegistry`` is the typed ``tools`` service contract for the plugin
system: ``register(tool)`` returns a disposer that removes the tool (and
restores the previously registered one on overwrite), so plugins can wire
unload cleanup with ``ctx.effect(tools.register(...))``.

The module-level functions delegate to the default instance
``TOOL_REGISTRY``; ``all_tools()`` returns a live snapshot for the agent.
``ALL_TOOLS`` is kept as a deprecated import-time alias. Built-in tools are
registered at import time.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .agent import AgentTool
from .base import Tool
from .bash import BashTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .read import ReadFileTool
from .write import WriteFileTool

log = logging.getLogger(__name__)


class ToolRegistry:
    """Typed tool registry — the ``tools`` service (register/get/all)."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Callable[[], None]:
        """Register a tool; returns a disposer that undoes the registration.

        Overwrite restores the previous entry when the disposer runs, so an
        unloaded plugin never leaves a hole where a built-in tool used to be.
        """
        previous = self._tools.get(tool.name)
        if previous is not None:
            log.warning("Tool %r re-registered, overwriting previous entry", tool.name)
        self._tools[tool.name] = tool

        def unregister() -> None:
            if self._tools.get(tool.name) is tool:
                if previous is not None:
                    self._tools[tool.name] = previous
                else:
                    self._tools.pop(tool.name, None)

        return unregister

    def unregister(self, name: str) -> None:
        """Remove a tool by name. Missing names are silently ignored."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        """Snapshot of all registered tools (new list each call)."""
        return list(self._tools.values())


TOOL_REGISTRY = ToolRegistry()
for _tool in (
    BashTool(),
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    GlobTool(),
    GrepTool(),
    AgentTool(),
):
    TOOL_REGISTRY.register(_tool)


def register_tool(tool: Tool) -> Callable[[], None]:
    """Register a tool on the default registry; returns a disposer."""
    return TOOL_REGISTRY.register(tool)


def unregister_tool(name: str) -> None:
    """Remove a tool by name from the default registry (idempotent)."""
    TOOL_REGISTRY.unregister(name)


def get_tool(name: str) -> Tool | None:
    """Look up a tool by name in the default registry."""
    return TOOL_REGISTRY.get(name)


def all_tools() -> list[Tool]:
    """Snapshot of the default registry (new list each call)."""
    return TOOL_REGISTRY.all()

# Deprecated compatibility alias: import-time snapshot.
ALL_TOOLS = all_tools()

__all__ = [
    "ALL_TOOLS",
    "TOOL_REGISTRY",
    "Tool",
    "ToolRegistry",
    "all_tools",
    "get_tool",
    "register_tool",
    "unregister_tool",
]
