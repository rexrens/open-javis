"""Tool contract and registry — the typed ``tools`` service for plugins.

This module is pure: it carries the tool interface every engine understands
and the registry that plugins mutate through ``ctx.get("tools")``.  Concrete
tools (bash/read/write/…) live with their engine implementation and subclass
:class:`Tool` from here, so plugin authors only ever depend on ``javis.contracts``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

log = logging.getLogger(__name__)


class Tool(ABC):
    """Minimal tool interface. Subclass this to add new capabilities."""

    name: str
    description: str
    parameters: ClassVar[dict[str, Any]]  # JSON Schema for the function args

    @abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Run the tool and return a text result."""
        ...

    def schema(self) -> dict[str, Any]:
        """OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    # -- concurrency metadata (WIP: no consumer yet) ----------------------

    @property
    def read_only(self) -> bool:
        """Whether this tool is side-effect free and safe to parallelize."""
        return False

    @property
    def exclusive(self) -> bool:
        """Whether this tool should run alone even if concurrency is enabled."""
        return False

    @property
    def concurrency_safe(self) -> bool:
        """Whether this tool can run alongside other concurrency-safe tools."""
        return self.read_only and not self.exclusive


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


__all__ = ["Tool", "ToolRegistry"]
