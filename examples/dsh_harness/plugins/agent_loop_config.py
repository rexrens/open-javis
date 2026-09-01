"""Plugin: loop-driver configuration (service ``"agentLoop"``).

dsh reads ``ctx.agentLoop.config.maxParallelToolCalls`` in the tool-call
scheduler; here the ``tools`` service reads it the same way.
"""


from pydantic import BaseModel, Field

from javis.harness.types import AgentLoop, AgentLoopConfig

name = "agent-loop-config"


class Config(BaseModel):
    max_parallel_tool_calls: int = Field(default=2, ge=1)


def apply(ctx, config):
    ctx.provide(
        "agentLoop",
        AgentLoop(AgentLoopConfig(max_parallel_tool_calls=config.max_parallel_tool_calls)),
    )
