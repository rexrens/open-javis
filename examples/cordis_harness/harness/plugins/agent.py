"""Provide ``ctx.agents``: the default agent driver.

The service owns every loop it starts, so unloading this row stops the agents
with the rest of the composition instead of leaving tasks behind.
"""

from __future__ import annotations

from pydantic import BaseModel

from harness.agent import AgentsService

name = "agent"
inject = ["llm", "tools", "sessions", "systemPrompt"]


class Config(BaseModel):
    """The default route for new sessions and the per-turn step budget."""

    provider: str = "openai"
    model: str = "deepseek-v4-flash"
    maxSteps: int = 12


def apply(ctx, config: Config):
    service = AgentsService(ctx, provider=config.provider, model=config.model, max_steps=config.maxSteps)
    ctx.provide("agents", service)

    def start():
        async def stop() -> None:
            await service.aclose()

        return stop

    return ctx.effect(start, "agents")
