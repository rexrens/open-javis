"""Interactive xli terminal UI for open-javis.

Each launch creates its own UI and Context. Slash commands are registered on that
UI instance before ``run()`` starts, and prompt handlers close over the same instance.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager

from dotenv import load_dotenv

from javis.agent.agent.agent import BaseReactAgent
from javis.agent.agent.protocol import (
    ReasoningDeltaEvent,
    ReasoningEndEvent,
    ReasoningStartEvent,
    SessionEvent,
    TextDeltaEvent,
    ToolCallEndEvent,
    ToolCallErrorEvent,
    ToolCallStartEvent,
    TurnEndEvent,
)
from javis.agent.model.model import Model
from javis.agent.model.openai.openai_chat import OpenAIChat
from javis.agent.tools.base import ToolRegister

from . import xli
from .commands import register_build_in_command
from .context import Context

__version__ = "0.1.0"


# --------------------------------------------------------------------------- UI helpers
# These take `ui` explicitly instead of reading a module-level global.
def _sync_status(ui: xli.UI[Context]) -> None:
    ctx = ui.context
    model = getattr(ctx.agent.model, "model", str(ctx.agent.model))
    ui.status.set(
        model=model,
        persona=ctx.agent.persona,
        session=ctx.session_id,
        turn=str(ctx.agent.turn),
        tokens=str(ctx.agent.tokens),
    )


async def _render_turn(
    ui: xli.UI[Context],
    ctx: Context,
    events: AsyncIterator[SessionEvent],
) -> None:
    """Render one agent turn, preserving the model's real streaming boundaries."""
    output: xli.Streaming | None = None
    stream: AbstractContextManager[xli.Streaming] | None = None
    reasoning_parts: list[str] = []
    tool_cards: dict[str, xli.Cell] = {}

    def close_stream() -> None:
        nonlocal output, stream
        if stream is not None:
            stream.__exit__(None, None, None)
        output = None
        stream = None

    try:
        async for event in events:
            if isinstance(event, TextDeltaEvent):
                if output is None:
                    stream = ui.streaming("assistant")
                    output = stream.__enter__()
                output.write(str(event.data.get("text", "")))

            elif isinstance(event, ReasoningStartEvent):
                close_stream()
                reasoning_parts = []

            elif isinstance(event, ReasoningDeltaEvent):
                reasoning_parts.append(str(event.data.get("text", "")))

            elif isinstance(event, ReasoningEndEvent):
                if reasoning_parts:
                    ui.reasoning("".join(reasoning_parts))
                reasoning_parts = []

            elif isinstance(event, ToolCallStartEvent):
                close_stream()
                call_id = str(event.data.get("id", ""))
                arguments = event.data.get("arguments", {})
                tool_cards[call_id] = ui.tool(
                    str(event.data.get("name", "tool")),
                    status="running",
                    args=arguments if isinstance(arguments, dict) else {"value": arguments},
                )

            elif isinstance(event, ToolCallEndEvent):
                close_stream()
                call_id = str(event.data.get("id", ""))
                card = tool_cards.pop(call_id, None)
                if card is None:
                    card = ui.tool(
                        str(event.data.get("name", "tool")),
                        args=None,
                        status="running",
                    )
                card.update(status="done", output=event.data.get("output", ""))

            elif isinstance(event, ToolCallErrorEvent):
                close_stream()
                call_id = str(event.data.get("id", ""))
                card = tool_cards.pop(call_id, None)
                if card is None:
                    card = ui.tool(
                        str(event.data.get("name", "tool")),
                        args=None,
                        status="running",
                    )
                card.update(status="error", error=str(event.data.get("error", "tool failed")))

            elif isinstance(event, TurnEndEvent):
                close_stream()
                _sync_status(ui)
    finally:
        close_stream()


async def cleanup() -> None:
    # Release resources / cancel inflight work. Don't write to the transcript here —
    # xli already prints an interrupt marker.
    await asyncio.sleep(0)


def _build_model() -> Model:
    """Build the real streaming model from environment configuration."""
    load_dotenv()
    api_key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("CORECODER_API_KEY")
    )
    base_url = (
        os.getenv("OPENAI_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or os.getenv("CORECODER_BASE_URL")
        or "https://api.openai.com/v1"
    )
    model_name = (
        os.getenv("OPENAI_MODEL")
        or os.getenv("DEEPSEEK_MODEL_NAME")
        or os.getenv("CORECODER_MODEL")
        or "gpt-4o-mini"
    )
    return OpenAIChat(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        timeout=60,
    )


def _create_ui(
    *,
    model: Model | None = None,
    tools: ToolRegister | None = None,
) -> xli.UI[Context]:
    ui = xli.UI(
        context=Context(
            agent=BaseReactAgent(
                model=model or _build_model(),
                tools=tools or ToolRegister(),
            ),
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
        await _render_turn(ui, ctx, ctx.agent.run(prompt))

    ui.on_prompt(reply)
    ui.on_interrupt(cleanup)
    _sync_status(ui)

    try:
        ui.run()
    finally:
        print("Bye")
