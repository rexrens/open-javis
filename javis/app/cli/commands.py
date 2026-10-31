"""Slash commands built into the open-javis CLI."""

import asyncio

from . import xli
from .context import Context


async def _cmd_tools(ui: xli.UI[Context], args: str) -> None:
    card = ui.tool("shell", status="running", args={"command": ["pytest", "-q"]})
    with ui.working("running tests"):
        await asyncio.sleep(1.2)
    card.update(status="done", output="......................\n54 passed in 0.12s")


async def _cmd_model(ui: xli.UI[Context], args: str) -> None:
    ui.note("choose what model and reasoning effort to use")


async def _cmd_permissions(ui: xli.UI[Context], args: str) -> None:
    ui.note("choose what Codex is allowed to do")


async def _cmd_session(ui: xli.UI[Context], args: str) -> None:
    ui.note("show seesion info and status")


def register_build_in_command(ui: xli.UI[Context]) -> None:
    ui.command("tools", description="run a tool and show its status")(_cmd_tools)
    ui.command("model", description="choose what model and reasoning effort to use")(_cmd_model)
    ui.command("permissions", description="choose what Codex is allowed to do")(_cmd_permissions)
    ui.command("seesion", description="show seesion info and status")(_cmd_session)
