"""Tool adapter — javis ``Tool`` (``javis.contracts.tools``) → dsh ``Tool``.

The javis tool contract is ``execute(*args, **kwargs) -> str`` (sync, runs on
the event-loop thread via ``asyncio.to_thread``); the dsh scheduler executes
async bodies with ``exclusive`` / ``parallel`` modes and ``tools/execute``
waterfalls.

Mapping:

- body — wraps ``javis_tool.execute(**arguments)`` in ``asyncio.to_thread``
  (javis tools are sync); exceptions become ``is_error`` text results.
- mode — ``exclusive`` when the javis tool declares it, else ``parallel``
  (the core's scheduler runs parallel calls in a bounded pool).
- schema — copied verbatim (``name`` / ``description`` / ``parameters``).
- ``AgentTool`` — its ``sub_agent_factory`` hook is wired by the engine
  (the old corecoder Agent no longer exists).
- ``AgentToolView`` — the loop-facing ``agentTools`` service: a live view
  over the host registry (reads adapt on the fly; ``register`` forwards).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from javis.contracts.tools import Tool as JavisTool
from javis.contracts.tools import ToolRegistry as JavisToolRegistry

from .tools import Tool as CoreTool
from .types import ExclusiveMode, ParallelMode, ToolExecutionResult, ToolSchema


def _invoke(javis_tool: JavisTool, arguments: Any) -> ToolExecutionResult:
    """Run one javis tool call (sync), mapping failures to error text results."""
    args = arguments or {}
    try:
        # Same pre-bind check the old corecoder agent used, so a TypeError
        # raised *inside* the tool isn't mislabelled as a bad-arguments error.
        inspect.signature(javis_tool.execute).bind(**args)
    except TypeError as exc:
        return ToolExecutionResult.text(
            f"Error: bad arguments for {javis_tool.name}: {exc}",
            is_error=True,
        )
    try:
        out = javis_tool.execute(**args)
        return ToolExecutionResult.text(str(out), is_error=False)
    except Exception as exc:  # noqa: BLE001 — tool errors are text for the model
        return ToolExecutionResult.text(
            f"Error executing {javis_tool.name}: {exc}",
            is_error=True,
        )


def adapt_tool(
    javis_tool: JavisTool,
    *,
    sub_agent_factory: Callable[[str], str] | None = None,
) -> CoreTool:
    """Adapt one javis tool to the core's tool contract."""

    async def body(exec_input: Any) -> ToolExecutionResult:
        # javis tools are sync: run on a worker thread so a long bash command
        # never blocks the event loop (the old corecoder did the same).
        return await asyncio.to_thread(_invoke, javis_tool, exec_input.arguments)

    mode = "exclusive" if getattr(javis_tool, "exclusive", False) else "parallel"
    tool = CoreTool(
        name=javis_tool.name,
        description=javis_tool.description,
        parameters=dict(javis_tool.parameters or {}),
        mode=mode,
        body=body,
    )
    # Wire the sub-agent spawner onto the javis AgentTool itself (the old
    # ``..agent`` import is gone; the engine injects the factory).
    if isinstance(javis_tool, JavisTool) and hasattr(javis_tool, "sub_agent_factory"):
        javis_tool.sub_agent_factory = sub_agent_factory
    return tool


class AgentToolView:
    """Live loop-facing view over the host's javis ``ToolRegistry``.

    Read operations delegate to the host registry on every call and adapt
    the result on the fly — a tool registered after this view was built is
    still visible to the loop. ``register`` forwards to the host registry,
    which stays the single source of truth.

    Why not a snapshot: the host ``tools`` service is a *javis* registry
    (``javis.contracts.tools``) while the loop needs *core* tools
    (``.tools``), and plugin rows may register tools in any order.
    """

    def __init__(
        self,
        host_registry: JavisToolRegistry,
        ctx: Any,
        *,
        sub_agent_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._host = host_registry
        self._ctx = ctx
        self._sub_agent_factory = sub_agent_factory

    def register(self, javis_tool: JavisTool) -> Any:
        """Register into the host registry (the single source of truth)."""
        return self._host.register(javis_tool)

    def get(self, name: str) -> CoreTool | None:
        javis_tool = self._host.get(name)
        return None if javis_tool is None else self._adapt(javis_tool)

    def all(self) -> list[CoreTool]:
        return [self._adapt(javis_tool) for javis_tool in self._host.all()]

    def schemas(self) -> list[ToolSchema]:
        return [tool.schema for tool in self.all()]

    def execution_mode(self, name: str) -> ExclusiveMode | ParallelMode:
        javis_tool = self._host.get(name)
        if javis_tool is not None and getattr(javis_tool, "exclusive", False):
            return ExclusiveMode()
        return ParallelMode()

    def _adapt(self, javis_tool: JavisTool) -> CoreTool:
        return adapt_tool(javis_tool, sub_agent_factory=self._sub_agent_factory)


__all__ = ["AgentToolView", "adapt_tool"]
