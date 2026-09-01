"""javis.tools — host-level tool registry and the seven built-in tools.

A shared service for the whole host (runtime ``tools`` service, plugin
registrations, engine consumption), independent of any engine implementation
(previously ``javis.engines.tools``; promoted 2026-09-01).

The registry itself lives in ``javis.contracts.tools`` (the typed ``tools``
service contract for the plugin system): ``register(tool)`` returns a
disposer that removes the tool (and restores the previously registered one on
overwrite), so plugins can wire unload cleanup with ``ctx.effect(...)``.

The module-level functions delegate to the default instance
``TOOL_REGISTRY``; ``all_tools()`` returns a live snapshot for the agent.
``ALL_TOOLS`` is kept as a deprecated import-time alias. Built-in tools are
registered at import time. ``create_default_tool_registry()`` builds a fresh
per-session registry (the runtime's ``tools`` service) so plugin tool
registrations never leak across sessions.

Migrated from ``javis.engines.corecoder.tools`` (2026-09-01): the
``AgentTool`` sub-agent dependency is now injected via
``AgentTool.sub_agent_factory`` instead of constructing the old corecoder
Agent directly.
"""

from __future__ import annotations

from collections.abc import Callable

from javis.contracts.tools import Tool, ToolRegistry

from .agent import AgentTool
from .bash import BashTool
from .edit import EditFileTool
from .glob import GlobTool
from .grep import GrepTool
from .read import ReadFileTool
from .write import WriteFileTool

_BUILTIN_TOOL_FACTORIES = (
    BashTool,
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    GlobTool,
    GrepTool,
    AgentTool,
)


def create_default_tool_registry() -> ToolRegistry:
    """Build a fresh registry preloaded with the seven built-in tools.

    Fresh instances per registry (not shared singletons) so a per-session
    registry can never carry state from another session — in particular the
    ``agent`` tool's parent wiring.
    """
    registry = ToolRegistry()
    for factory in _BUILTIN_TOOL_FACTORIES:
        registry.register(factory())
    return registry


TOOL_REGISTRY = create_default_tool_registry()


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
    "create_default_tool_registry",
    "get_tool",
    "register_tool",
    "unregister_tool",
]
