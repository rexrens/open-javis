"""Runtime assembly and request dispatch for javis.

This is the javis equivalent of ``openharness.ui.runtime`` — but stripped of
MCP, hooks, permissions, bridge, tasks, coordinator, auth, sandbox, plugins,
themes and output-styles. What remains:

- ``RuntimeBundle`` — engine + commands + app_state + session_backend
- ``build_javis_runtime`` — assembles a bundle with an ``AgentEngine``
- ``handle_line`` — the single dispatch point (slash commands + agent turns)
- ``run_javis_print_mode`` — non-interactive single-prompt mode

Configuration lives in ``javis.session.config`` (spec/config.md v2); the
system-prompt builder (``build_javis_system_prompt``) lives here.

``handle_line`` yields ``AgentEvent`` straight through to the host's
``render_event`` callback — no ``StreamEvent`` translation layer.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from javis.commands.registry import CommandContext, CommandRegistry, create_default_command_registry
from javis.contracts.engine import AgentEngine
from javis.contracts.messages import ConversationMessage, sanitize_conversation_messages
from javis.contracts.types import AgentError, AgentEvent, AgentStatus, AgentTextDelta, AgentTurnEnd
from javis.session.session_storage import JavisSessionBackend
from javis.session.state import AppState, AppStateStore
from javis.session.workspace import initialize_workspace

SystemPrinter = Callable[[str], Awaitable[None]]
StreamRenderer = Callable[[AgentEvent], Awaitable[None]]
ClearHandler = Callable[[], Awaitable[None]]


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def build_javis_system_prompt(cwd: str | Path | None = None, *, workspace: str | Path | None = None) -> str:
    """Return a short system prompt for the agent."""
    del cwd, workspace  # signature kept for parity; stored on the engine
    return (
        "You are javis, an agent running on the javis TUI.\n\n"
        "You are backed by an ``AgentEngine`` implementation. Your responses "
        "stream through the React terminal frontend via the JSON-lines wire "
        "protocol."
    )


@dataclass
class RuntimeBundle:
    """Everything the host needs to drive one interactive session."""

    engine: AgentEngine
    cwd: str
    app_state: AppStateStore
    commands: CommandRegistry
    session_backend: JavisSessionBackend
    session_id: str
    system_prompt: str = ""
    settings_overrides: dict[str, Any] = field(default_factory=dict)


async def build_javis_runtime(
    *,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    system_prompt: str | None = None,
    engine: AgentEngine | None = None,
    restore_messages: list[dict[str, Any]] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
    session_backend: JavisSessionBackend | None = None,
    workspace: str | Path | None = None,
) -> RuntimeBundle:
    """Assemble a ``RuntimeBundle`` backed by an ``AgentEngine``.

    The built-in ``CoreCoderEngine`` is constructed directly from config
    unless ``engine=...`` is passed explicitly (used by tests to inject a
    fake engine).
    """
    cwd_resolved = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
    workspace_root = initialize_workspace(workspace)
    system_prompt_text = system_prompt or build_javis_system_prompt(cwd_resolved, workspace=workspace_root)

    tool_metadata: dict[str, Any] = {
        "permission_mode": "default",
        "session_id": "",
    }
    if isinstance(restore_tool_metadata, dict):
        tool_metadata.update(restore_tool_metadata)

    session_id = uuid4().hex[:12]
    tool_metadata["session_id"] = session_id

    from javis.session.config import load_config

    # load_config deep-merges global + project config.json; raises ValueError on
    # malformed JSON (config errors are surfaced, not swallowed). Returns a
    # JavisConfig with defaults otherwise — cfg is never None after this.
    cfg = load_config(cwd=cwd_resolved, workspace=workspace_root)
    commands = create_default_command_registry()

    engine_max_turns = max_turns
    if engine is None:
        from javis.engines.corecoder.engine import CoreCoderEngine
        from javis.session.config import resolve_provider_and_model
        from javis.session.credentials import resolve_api_key

        provider_name, model_id = resolve_provider_and_model(cfg, cli_model=model)
        provider_cfg = cfg.providers[provider_name]
        api_key = resolve_api_key(
            provider_name,
            provider_cfg.api_key_env,
            provider_cfg.api_key,
            workspace=workspace_root,
            cwd=cwd_resolved,
        )
        max_tokens: int | None = None
        for m in provider_cfg.models:
            if m.id == model_id:
                max_tokens = m.max_tokens
                break
        if engine_max_turns is None and cfg.session.max_turns is not None:
            engine_max_turns = cfg.session.max_turns
        engine_obj = CoreCoderEngine.build(
            model=model_id,
            api_key=api_key or "",
            base_url=provider_cfg.base_url,
            max_tokens=max_tokens,
            system_prompt=system_prompt_text,
            cwd=cwd_resolved,
            max_turns=engine_max_turns,
            tool_metadata=tool_metadata,
        )
    else:
        engine_obj = engine
        # explicit CLI overrides win over the injected engine's defaults
        if model is not None and hasattr(engine_obj, "set_model"):
            engine_obj.set_model(model)
        if system_prompt is not None and hasattr(engine_obj, "set_system_prompt"):
            engine_obj.set_system_prompt(system_prompt)

    model_name = model or getattr(engine_obj, "model", None) or "unknown"

    if restore_messages:
        restored = sanitize_conversation_messages(
            [ConversationMessage.model_validate(m) for m in restore_messages]
        )
        engine_obj.load_messages(restored)

    app_state = AppStateStore(
        AppState(
            model=model_name,
            cwd=cwd_resolved,
            permission_mode=cfg.session.permission_mode if cfg else "default",
            theme=cfg.appearance.theme if cfg else "default",
            provider="javis",
            auth_status="ok",
            fast_mode=cfg.session.fast_mode if cfg else False,
            vim_enabled=cfg.editor.vim_enabled if cfg else False,
            output_style=cfg.appearance.output_style if cfg else "default",
        )
    )

    return RuntimeBundle(
        engine=engine_obj,
        cwd=cwd_resolved,
        app_state=app_state,
        commands=commands if cfg is not None else create_default_command_registry(),
        session_backend=session_backend or JavisSessionBackend(workspace_root),
        session_id=session_id,
        system_prompt=system_prompt_text,
    )


def _save_session(bundle: RuntimeBundle) -> None:
    """Persist the current conversation to the session backend."""
    bundle.session_backend.save_snapshot(
        cwd=bundle.cwd,
        model=bundle.engine.model,
        system_prompt=bundle.engine.system_prompt,
        messages=bundle.engine.messages,
        usage=bundle.engine.total_usage,
        session_id=bundle.session_id,
        tool_metadata=bundle.engine.tool_metadata,
    )


async def handle_line(
    bundle: RuntimeBundle,
    line: str,
    *,
    print_system: SystemPrinter,
    render_event: StreamRenderer,
    clear_output: ClearHandler,
    user_message: ConversationMessage | None = None,
) -> bool:
    """Handle one submitted line. Returns ``True`` to continue, ``False`` to exit."""
    parsed = None if user_message is not None else bundle.commands.lookup(line)
    if parsed is not None:
        command, args = parsed
        context = CommandContext(
            engine=bundle.engine,
            app_state=bundle.app_state,
            cwd=bundle.cwd,
            session_id=bundle.session_id,
        )
        result = await command.handler(args, context)
        if result.clear_screen:
            await clear_output()
        if result.message:
            await print_system(result.message)
        if result.replay_messages:
            await clear_output()
            await print_system("Session restored:")
            for msg in result.replay_messages:
                if msg.role == "user":
                    await print_system(f"> {msg.text}")
                elif msg.role == "assistant" and msg.text.strip():
                    async for event in _replay_assistant(msg):
                        await render_event(event)
        if result.submit_prompt:
            async for event in bundle.engine.submit_message(result.submit_prompt):
                await render_event(event)
            _save_session(bundle)
        return not result.should_exit

    # Normal prompt — feed it to the engine.
    async for event in bundle.engine.submit_message(user_message or line):
        await render_event(event)
    _save_session(bundle)
    return True


async def _replay_assistant(message: ConversationMessage) -> AsyncIterator[AgentEvent]:
    """Replay a restored assistant message as a text delta + turn end."""
    yield AgentTextDelta(text=message.text)
    yield AgentTurnEnd(text=message.text)


async def run_javis_print_mode(
    *,
    prompt: str,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
) -> int:
    """Run a single prompt and print the assistant output to stdout."""
    cwd_path = str(Path(cwd or Path.cwd()).resolve())
    previous_cwd = Path.cwd()
    os.chdir(cwd_path)
    
    try:
        bundle = await build_javis_runtime(
            cwd=cwd_path,
            model=model,
            max_turns=max_turns,
            workspace=workspace,
        )

        async def _print_system(message: str) -> None:
            print(message, file=sys.stderr)

        saw_error = False
        async def _render_event(event: AgentEvent) -> None:
            nonlocal saw_error
            if isinstance(event, AgentTextDelta):
                sys.stdout.write(event.text)
                sys.stdout.flush()
            elif isinstance(event, AgentTurnEnd):
                sys.stdout.write("\n")
                sys.stdout.flush()
            elif isinstance(event, AgentError):
                saw_error = True
                print(event.message, file=sys.stderr)
            elif isinstance(event, AgentStatus):
                print(event.message, file=sys.stderr)
            # Tool start/result events are not printed in print mode.

        async def _clear_output() -> None:
            return None

        await handle_line(
            bundle,
            prompt,
            print_system=_print_system,
            render_event=_render_event,
            clear_output=_clear_output,
            # Print mode is a plain prompt: never dispatch slash commands.
            user_message=ConversationMessage.from_user_text(prompt),
        )
        return 1 if saw_error else 0
    finally:
        os.chdir(previous_cwd)


__all__ = [
    "RuntimeBundle",
    "build_javis_runtime",
    "build_javis_system_prompt",
    "handle_line",
    "run_javis_print_mode",
]
