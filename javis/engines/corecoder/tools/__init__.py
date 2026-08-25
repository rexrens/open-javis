"""Tool registry.

The registry is a ``ToolRegistry`` instance — the same instance javis hands
to plugins as the ``tools`` service, so plugin-registered tools reach the
engine via ``all_tools()``. ``register`` returns a disposer (``unregister``),
so plugin teardown is uniform: ``ctx.effect(tools.register(tool))``.

``TOOL_REGISTRY`` is the shared default instance (built-ins registered at
import time). The module-level functions (``register_tool`` / ``get_tool`` /
``all_tools`` / ``unregister_tool``) delegate to it and are kept for
compatibility; ``ALL_TOOLS`` remains a deprecated import-time alias.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

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
    """Mutable tool table. ``register`` returns an unregister disposer."""

    def __init__(self, seed: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in seed or ():
            self.register(tool)

    def register(self, tool: Tool) -> Callable[[], None]:
        """Register a tool; re-registration overwrites with a warning
        (idempotent). Returns a disposer that unregisters it."""
        if tool.name in self._tools:
            log.warning("Tool %r re-registered, overwriting previous entry", tool.name)
        self._tools[tool.name] = tool
        return lambda: self.unregister(tool.name)

    def unregister(self, name: str) -> None:
        """Remove a tool by name. Missing names are silently ignored."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        """Snapshot of all registered tools (new list each call)."""
        return list(self._tools.values())


# Shared default instance: what the engine reads (all_tools) and what the
# plugin "tools" service points at.
TOOL_REGISTRY = ToolRegistry(
    seed=(
        BashTool(),
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        GlobTool(),
        GrepTool(),
        AgentTool(),
    )
)


def register_tool(tool: Tool) -> Callable[[], None]:
    """Register a tool on the shared registry (compat: returns the disposer)."""
    return TOOL_REGISTRY.register(tool)


def unregister_tool(name: str) -> None:
    """Remove a tool from the shared registry. Missing names are ignored."""
    TOOL_REGISTRY.unregister(name)


def get_tool(name: str) -> Tool | None:
    """Look up a tool in the shared registry by name."""
    return TOOL_REGISTRY.get(name)


def all_tools() -> list[Tool]:
    """Snapshot of all registered tools (new list each call)."""
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
