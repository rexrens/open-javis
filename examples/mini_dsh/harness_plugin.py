"""Cordis entry for the plugin-harness example — the composition root.

This module is loaded by javis' Cordis loader as a standalone file plugin
(``name: ./harness_plugin.py`` in cordis.yml), so it cannot use relative
imports. It prepends its own directory to ``sys.path`` and imports the
harness + providers as plain modules — copy this folder anywhere and point
``--plugins`` at its cordis.yml.

Responsibilities (composition root):

1. read the built-in services (``config`` / ``tools`` / ``commands`` / ``host``);
2. translate javis config into a ``ChatProvider`` (scripted vs OpenAI-compatible);
3. build ``HarnessEngine`` with a snapshot of the tools registry;
4. ``ctx.provide(ENGINE_SERVICE, engine)``;
5. register a ``/harness`` status command + cleanup effects.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from harness import HarnessEngine
from providers import (
    OpenAICompatChatProvider,
    ScriptedProvider,
    ScriptedTurn,
    ToolCallDraft,
)

from javis.commands.registry import Command, CommandContext, CommandResult
from javis.contracts import (
    COMMANDS_SERVICE,
    CONFIG_SERVICE,
    ENGINE_SERVICE,
    HOST_SERVICE,
    TOOLS_SERVICE,
)
from javis.session.config import JavisConfig, resolve_provider_and_model
from javis.session.credentials import resolve_api_key


class Config(BaseModel):
    """Entry config from cordis.yml (the ``config:`` field)."""

    provider: str = "auto"  # auto | scripted | openai


def _demo_script(workspace: str) -> list[ScriptedTurn]:
    """Offline demo: write a note with a plugin tool, read it back, conclude."""
    note_path = Path(workspace) / "notes.txt"  # where WorkspaceNoteTool appends
    return [
        ScriptedTurn(
            reasoning=(
                "The user wants to see the plugin harness in action. "
                "I will use the workspace_note tool from the extra-tools plugin."
            ),
            content="I'll save a note to your workspace using the plugin tool.",
            tool_calls=[
                ToolCallDraft(
                    id="demo-note",
                    name="workspace_note",
                    arguments={"content": "Hello from the plugin harness demo"},
                )
            ],
        ),
        ScriptedTurn(
            content="Let me read the note back to confirm.",
            tool_calls=[
                ToolCallDraft(
                    id="demo-read",
                    name="read_file",
                    arguments={"file_path": str(note_path)},
                )
            ],
        ),
        ScriptedTurn(
            content=(
                "Confirmed: the note now contains “Hello from the plugin harness demo”. "
                "This reply came from a standalone harness loaded as a cordis plugin."
            )
        ),
    ]


def _resolve_provider(config: Config, cfg: JavisConfig, host: Any) -> tuple[Any, str]:
    """Pick a ChatProvider: scripted (offline) or OpenAI-compatible (live)."""
    provider_name, model_id = resolve_provider_and_model(cfg, cli_model=host.model_override)
    provider_cfg = cfg.providers[provider_name]
    choice = config.provider or os.environ.get("HARNESS_PROVIDER", "auto")
    if choice == "scripted":
        return ScriptedProvider(_demo_script(host.workspace), model=model_id), model_id
    api_key = resolve_api_key(
        provider_name,
        provider_cfg.api_key_env,
        provider_cfg.api_key,
        workspace=host.workspace,
        cwd=host.cwd,
    )
    if choice == "openai" or (choice == "auto" and api_key):
        return (
            OpenAICompatChatProvider(
                model=model_id,
                api_key=api_key or "",
                base_url=provider_cfg.base_url,
            ),
            model_id,
        )
    # auto + no key → offline demo
    return ScriptedProvider(_demo_script(host.workspace), model=model_id), model_id


def apply(ctx: Any, config: Config) -> None:
    cfg: JavisConfig = ctx.get(CONFIG_SERVICE)
    tools = ctx.get(TOOLS_SERVICE)
    commands = ctx.get(COMMANDS_SERVICE)
    host = ctx.get(HOST_SERVICE)

    provider, model_id = _resolve_provider(config, cfg, host)
    engine = HarnessEngine(
        model=model_id,
        provider=provider,
        tools=tools.all(),
        system_prompt=host.system_prompt,
        cwd=host.cwd,
        workspace=host.workspace,
        session_id=host.session_id,
        max_turns=host.max_turns_override,
        tool_metadata=host.tool_metadata,
    )

    async def _harness_status(_: str, context: CommandContext) -> CommandResult:
        del context
        return CommandResult(
            message=(
                f"harness: {engine.harness_name}\n"
                f"tools: {', '.join(sorted(engine.tools))}\n"
                f"session: {engine.session_id}\n"
                f"messages: {len(engine.messages)}"
            )
        )

    ctx.provide(ENGINE_SERVICE, engine)
    # register() returns a disposer; effect wires it to plugin unload
    ctx.effect(lambda: commands.register(Command("harness", "Show plugin-harness status", _harness_status)))
    ctx.effect(lambda: getattr(provider, "close", lambda: None))
