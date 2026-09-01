"""Tool adapter — javis ``Tool`` (``javis.contracts.tools``) → harness-core ``Tool``.

The javis tool contract is ``execute(*args, **kwargs) -> str`` (sync, runs on
the event-loop thread in the old corecoder via ``asyncio.to_thread``); the
harness core's scheduler executes async bodies with ``exclusive`` /
``parallel`` modes and ``tools/execute`` waterfalls.

Mapping:

- body — wraps ``javis_tool.execute(**arguments)`` in ``asyncio.to_thread``
  (javis tools are sync); exceptions become ``is_error`` text results.
- mode — ``exclusive`` when the javis tool declares it, else ``parallel``
  (the core's scheduler runs parallel calls in a bounded pool).
- schema — copied verbatim (``name`` / ``description`` / ``parameters``).
- ``AgentTool`` — its ``sub_agent_factory`` hook is wired by the engine
  (the old corecoder Agent no longer exists).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from javis.contracts.tools import Tool as JavisTool
from javis.contracts.tools import ToolRegistry as JavisToolRegistry

from .core.contracts import ToolExecutionResult
from .core.tools import Tool as CoreTool
from .core.tools import ToolRegistry as CoreToolRegistry


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


def adapt_registry(
    javis_registry: JavisToolRegistry,
    ctx: Any,
    *,
    sub_agent_factory: Callable[[str], str] | None = None,
) -> CoreToolRegistry:
    """Build the core's tool registry from a javis registry snapshot.

    Called by the engine at build time, AFTER plugins loaded, so
    plugin-registered tools are included (the runtime passes its ``tools``
    service here).
    """
    registry = CoreToolRegistry(ctx)
    for javis_tool in javis_registry.all():
        registry.register(
            adapt_tool(javis_tool, sub_agent_factory=sub_agent_factory)
        )
    return registry


__all__ = ["adapt_registry", "adapt_tool"]
