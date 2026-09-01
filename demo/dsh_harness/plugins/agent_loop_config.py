"""Plugin: loop-driver configuration (service ``"agentLoop"``).

dsh reads ``ctx.agentLoop.config.maxParallelToolCalls`` in the tool-call
scheduler; here the ``tools`` service reads it the same way.
"""

import os as _os
import sys as _sys

_DEMO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _DEMO_ROOT not in _sys.path:
    _sys.path.insert(0, _DEMO_ROOT)

from dsh_harness.contracts import AgentLoop, AgentLoopConfig
from pydantic import BaseModel, Field

name = "agent-loop-config"


class Config(BaseModel):
    max_parallel_tool_calls: int = Field(default=2, ge=1)


def apply(ctx, config):
    ctx.provide(
        "agentLoop",
        AgentLoop(AgentLoopConfig(max_parallel_tool_calls=config.max_parallel_tool_calls)),
    )
