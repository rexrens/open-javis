"""Sub-agent spawning (inspired by Claude Code's AgentTool, 1397 lines).

The idea: for complex sub-tasks, spawn an independent agent with its own
conversation history and tool access. This lets the main agent delegate
work like "go research this codebase and report back" without polluting
its own context window.

The sub-agent runs to completion and returns a text summary.

The old corecoder implementation constructed a ``corecoder.Agent`` directly
from ``..agent``. The harness engine owns a different loop (ReactLoopAgent),
so the spawner is injected instead: the engine sets ``sub_agent_factory``
(a ``(task) -> str`` callable that runs one sub-task to completion and
returns its final text).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from .base import Tool


class AgentTool(Tool):
    name = "agent"
    description = (
        "Spawn a sub-agent to handle a complex sub-task independently. "
        "The sub-agent has its own context and tool access. Use this for: "
        "researching a codebase, implementing a multi-step change in isolation, "
        "or any task that would benefit from a fresh context window."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "What the sub-agent should accomplish",
            },
        },
        "required": ["task"],
    }

    #: Injected by the harness engine: ``(task) -> str`` runs a sub-agent to
    #: completion and returns its final text (the old corecoder Agent is gone).
    sub_agent_factory: Callable[[str], str] | None = None

    def execute(self, task: str, **kwargs: Any) -> str:
        if self.sub_agent_factory is None:
            return "Error: agent tool not initialized (no sub-agent factory)"

        try:
            result = self.sub_agent_factory(task)
            # trim long results to avoid blowing up parent's context
            if len(result) > 5000:
                result = result[:4500] + "\n... (sub-agent output truncated)"
            return f"[Sub-agent completed]\n{result}"
        except Exception as e:  # noqa: BLE001 — tool errors are returned as text for the LLM
            return f"Sub-agent error: {e}"
