"""Tool registry.

Tools register themselves via ``register_tool``; ``all_tools()`` returns a
snapshot for the agent. ``ALL_TOOLS`` is kept as a deprecated import-time
alias. Built-in tools are registered at import time.
"""

from __future__ import annotations

import logging

from .agent import AgentTool
from .base import Tool
from .bash import BashTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .read import ReadFileTool
from .write import WriteFileTool

log = logging.getLogger(__name__)

_TOOLS: dict[str, Tool] = {}


def register_tool(tool: Tool) -> None:
    """Register a tool. Re-registration overwrites with a warning (idempotent)."""
    if tool.name in _TOOLS:
        log.warning("Tool %r re-registered, overwriting previous entry", tool.name)
    _TOOLS[tool.name] = tool


def get_tool(name: str) -> Tool | None:
    """Look up a tool by name."""
    return _TOOLS.get(name)


def all_tools() -> list[Tool]:
    """Snapshot of all registered tools (new list each call)."""
    return list(_TOOLS.values())


for _tool in (
    BashTool(),
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    GlobTool(),
    GrepTool(),
    AgentTool(),
):
    register_tool(_tool)

# Deprecated compatibility alias: import-time snapshot.
ALL_TOOLS = all_tools()

__all__ = ["ALL_TOOLS", "Tool", "all_tools", "get_tool", "register_tool"]
