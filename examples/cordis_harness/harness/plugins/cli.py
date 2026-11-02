"""Mount the interactive REPL as a plugin and own the process exit path."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from harness import options
from harness.repl import Repl

name = "cli"
inject = ["llm", "tools", "sessions", "agents"]


class Config(BaseModel):
    """Front-end behaviour; ``cdh`` flags override every field."""

    cwd: str | None = None
    resume: str | None = None
    provider: str | None = None
    model: str | None = None
    autoApprove: bool = False
    showReasoning: bool = False
    listSessions: bool = False


def apply(ctx, config: Config):
    overrides = {key: value for key, value in options.LAUNCH.snapshot().items() if key in Config.model_fields}
    merged = config.model_copy(update=overrides)
    repl = Repl(
        ctx,
        cwd=merged.cwd,
        resume=merged.resume,
        provider=merged.provider,
        model=merged.model,
        auto_approve=merged.autoApprove,
        show_reasoning=merged.showReasoning,
        list_sessions=merged.listSessions,
    )
    # ``cli.run`` waits for ``app/exit`` instead of owning the terminal when
    # this service is present.
    ctx.provide("repl", repl)

    def start():
        task = asyncio.ensure_future(repl.run())

        async def stop() -> None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except BaseException as error:  # noqa: BLE001 - reported, never lost
                ctx.logger.error("cli: REPL exited with %s", error)

        return stop

    ctx.effect(start, "repl")
