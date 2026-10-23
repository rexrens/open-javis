"""Interactive xli terminal UI for open-javis.

Each launch creates its own UI and Context. Slash commands are registered on that
UI instance before ``run()`` starts, and prompt handlers close over the same instance.
"""

from __future__ import annotations

import asyncio
import uuid

from . import xli
from .commands import register_build_in_command
from .context import Context
from .fake import Event, FakeAgent

__version__ = "0.1.0"


# --------------------------------------------------------------------------- UI helpers
# These take `ui` explicitly instead of reading a module-level global.
def _sync_status(ui: xli.UI[Context]) -> None:
    ctx = ui.context
    ui.status.set(
        model=ctx.agent.model,
        persona=ctx.agent.persona,
        session=ctx.session_id,
        turn=str(ctx.agent.turn),
        tokens=str(ctx.agent.tokens),
    )


async def _render(ui: xli.UI[Context], ctx: Context, ev: Event) -> None:
    """Dispatch one typed event to the matching xli primitive."""
    if ev.kind == "reasoning":
        ui.reasoning(ev.payload)

    elif ev.kind == "note":
        ui.note(ev.payload)

    elif ev.kind == "plan":
        ui.plan(ev.payload)

    elif ev.kind == "diff":
        ui.diff(ev.payload, path="greet.py")

    elif ev.kind == "tool":
        t = ev.payload
        card = ui.tool(t["name"], status="running", args=t.get("args"))
        with ui.working("running"):
            await asyncio.sleep(0.5)
        card.update(status="done", output=t.get("output", "ok"))

    elif ev.kind == "text":
        # stream word-by-word — the "yield"-style streaming loop
        with ui.streaming("assistant") as out:
            for word in ev.payload.split(" "):
                ctx.agent.tokens += 1
                out.write(word + " ")
                ui.status.set(tokens=str(ctx.agent.tokens))
                await asyncio.sleep(0.2)


async def cleanup() -> None:
    # Release resources / cancel inflight work. Don't write to the transcript here —
    # xli already prints an interrupt marker.
    await asyncio.sleep(0)


def _create_ui() -> xli.UI[Context]:
    ui = xli.UI(
        context=Context(
            agent=FakeAgent(model="model", persona="persona"),
            session_id=uuid.uuid4().hex[:8],
        ),
        title="open-javis",
        status_fields=["model", "persona", "session", "turn", "tokens"],
        pet="owl",
    )
    register_build_in_command(ui)

    return ui


# --------------------------------------------------------------------------- entrypoint
def launch_cli() -> None:
    ui = _create_ui()

    async def reply(prompt: str) -> None:
        # xli calls the prompt handler with only the prompt string, so we close over
        # the locally-created `ui` here.
        ctx = ui.context
        async for ev in ctx.agent.run(prompt):
            await _render(ui, ctx, ev)

    ui.on_prompt(reply)
    ui.on_interrupt(cleanup)
    _sync_status(ui)

    ui.run()
