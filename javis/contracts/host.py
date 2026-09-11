"""Host runtime context — the ``host`` service exposed to plugins.

When the runtime assembles a session it provides this object under the
``HOST_SERVICE`` name.  A composition row (the ``harness`` row) reads it
(together with ``config`` and ``agentTools``) to construct the ``Harness``
instance inside ``apply`` (the built-in row calls ``build_harness(ctx)``):

    def apply(ctx):
        host = ctx.get("host")        # HostContext
        cfg = ctx.get("config")       # JavisConfig
        tools = ctx.get("agentTools") # ToolRegistry
        ctx.provide("harness", build_my_harness(cfg, tools.all(), host))

Values that exist per session (cwd, session_id, tool_metadata) are runtime
facts and can never be baked into a static composition file, so they arrive
through this service rather than plugin config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HostContext:
    """Per-session host facts handed to plugins at build time."""

    cwd: str
    workspace: str
    session_id: str
    tool_metadata: dict[str, Any] = field(default_factory=dict)
    model_override: str | None = None
    max_turns_override: int | None = None
    system_prompt: str = ""


__all__ = ["HostContext"]
