"""Host runtime context — the ``host`` service exposed to plugins.

When the runtime assembles a session it provides this object under the
``HOST_SERVICE`` name.  An engine plugin reads it (together with ``config``
and ``tools``) to construct its ``AgentEngine`` instance inside ``apply``:

    def apply(ctx):
        host = ctx.get("host")       # HostContext
        cfg = ctx.get("config")      # JavisConfig
        tools = ctx.get("tools")     # ToolRegistry
        ctx.provide("engine", build_engine(cfg, tools.all(), host))

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
