"""Plugin: the ``"tools"`` service + the demo's mock tools.

Provides a :class:`~dsh_harness.tools.ToolRegistry` and registers four tools
covering the scheduler's semantics:

- ``now``         — parallel mode, trivial body
- ``weather``     — parallel mode, mock city table (error path for unknown cities)
- ``set_note``    — **exclusive** mode: a barrier that serializes around it
- ``end_session`` — **concludesTurn**: the turn completes right after its result

Registration is reversible: every ``register`` returns a disposer tracked by
the plugin fiber, so unloading the fiber restores an empty registry.
"""

import os as _os
import sys as _sys

_DEMO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _DEMO_ROOT not in _sys.path:
    _sys.path.insert(0, _DEMO_ROOT)

from dsh_harness.contracts import TextBlock, ToolExecutionResult
from dsh_harness.tools import Tool, ToolRegistry

name = "demo-tools"

CITIES: dict[str, tuple[int, str]] = {
    "Paris": (18, "light rain"),
    "Tokyo": (24, "sunny"),
    "London": (12, "cloudy"),
    "New York": (29, "humid"),
}

FIXED_NOW = "2026-08-31T18:00:00Z"


def _now(_exec):
    return FIXED_NOW


def _weather(exec):
    city = str(exec.arguments.get("city", "")).strip()
    if city in CITIES:
        temp, condition = CITIES[city]
        return f"{city}: {temp}C, {condition}"
    return ToolExecutionResult.text(f"Error: unknown city {city!r}", is_error=True)


def _set_note(exec):
    text = str(exec.arguments.get("text", ""))
    return ToolExecutionResult.text(f"note saved: {text}")


def _end_session(_exec):
    # concludesTurn: the driver stops after committing this result.
    return ToolExecutionResult(content=[TextBlock("session ended by tool")], concludes_turn=True)


def apply(ctx):
    registry = ToolRegistry(ctx)
    ctx.provide("tools", registry)
    registry.register(
        Tool(
            "now",
            "Return the current UTC time (mock).",
            parameters={"type": "object", "properties": {}},
            mode="parallel",
            body=_now,
        )
    )
    registry.register(
        Tool(
            "weather",
            "Return the (mock) weather for a city.",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            mode="parallel",
            body=_weather,
        )
    )
    registry.register(
        Tool(
            "set_note",
            "Save a workspace note (exclusive tool: runs as a barrier).",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            mode="exclusive",
            body=_set_note,
        )
    )
    registry.register(
        Tool(
            "end_session",
            "End the session; the turn concludes right after this result.",
            parameters={"type": "object", "properties": {}},
            mode="exclusive",
            body=_end_session,
        )
    )
