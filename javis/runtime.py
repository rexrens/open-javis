"""Runtime assembly and request dispatch for javis.

This is the javis equivalent of ``openharness.ui.runtime`` — but stripped of
MCP, hooks, permissions, bridge, tasks, coordinator, auth, sandbox, plugins,
themes and output-styles. What remains:

- ``RuntimeBundle`` — engine + commands + app_state + session_backend
- ``build_javis_runtime`` — assembles a bundle with a ``MockEngine``
- ``handle_line`` — the single dispatch point (slash commands + agent turns)
- ``start_runtime`` / ``close_runtime`` — lifecycle hooks (currently no-ops)
- ``run_javis_print_mode`` — non-interactive single-prompt mode

``handle_line`` yields ``AgentEvent`` straight through to the host's
``render_event`` callback — no ``StreamEvent`` translation layer.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from javis.commands import CommandContext, CommandRegistry, create_default_command_registry
from javis.engine.mock_engine import MockEngine
from javis.engine.protocol import AgentBackend
from javis.engine.types import AgentEvent, AgentTextDelta, AgentTurnEnd, AgentError, AgentStatus
from javis.messages import ConversationMessage, sanitize_conversation_messages
from javis.prompts import build_javis_system_prompt
from javis.session_storage import JavisSessionBackend
from javis.state import AppState, AppStateStore
from javis.workspace import initialize_workspace

SystemPrinter = Callable[[str], Awaitable[None]]
StreamRenderer = Callable[[AgentEvent], Awaitable[None]]
ClearHandler = Callable[[], Awaitable[None]]


@dataclass
class RuntimeBundle:
    """Everything the host needs to drive one interactive session."""

    engine: MockEngine
    cwd: str
    app_state: AppStateStore
    commands: CommandRegistry
    session_backend: JavisSessionBackend
    session_id: str
    system_prompt: str = ""
    settings_overrides: dict[str, Any] = field(default_factory=dict)


def build_javis_backend_command(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
) -> list[str]:
    """Return the command the React frontend will spawn to start the backend."""
    command = [sys.executable, "-m", "javis", "--backend-only"]
    if cwd:
        command.extend(["--cwd", cwd])
    if workspace:
        command.extend(["--workspace", str(workspace)])
    if model:
        command.extend(["--model", model])
    if max_turns is not None:
        command.extend(["--max-turns", str(max_turns)])
    return command


async def build_javis_runtime(
    *,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    system_prompt: str | None = None,
    agent_backend: AgentBackend | None = None,
    engine: str | None = None,
    restore_messages: list[dict] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
    session_backend: JavisSessionBackend | None = None,
    workspace: str | Path | None = None,
) -> RuntimeBundle:
    """Assemble a ``RuntimeBundle`` backed by a ``MockEngine``.

    The agent backend is resolved in one of two ways:
      - ``agent_backend=...`` — explicit backend (used by tests)
      - ``engine=...`` — named engine via the registry (config.json / env
        fall back to the default engine when omitted)
    Passing both raises ``ValueError``.
    """
    if engine is not None and agent_backend is not None:
        raise ValueError("Pass either engine= or agent_backend=, not both")

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

    if agent_backend is None:
        from javis.config import load_config, resolve_engine_name
        from javis.engines import create_agent_backend, get_engine_config

        config_data = load_config(workspace_root)
        engine_name = resolve_engine_name(engine, config_data)
        agent_backend = create_agent_backend(
            engine_name,
            model=model,
            system_prompt=system_prompt_text,
            cwd=cwd_resolved,
            max_turns=max_turns,
            tool_metadata=tool_metadata,
            engine_config=get_engine_config(engine_name, config_data),
        )

    model_name = model or getattr(agent_backend, "model", None) or "javis-mock"

    engine_obj = MockEngine(
        agent_backend=agent_backend,
        model=model_name,
        system_prompt=system_prompt_text,
        cwd=cwd_resolved,
        max_turns=max_turns,
        tool_metadata=tool_metadata,
    )

    if restore_messages:
        restored = sanitize_conversation_messages(
            [ConversationMessage.model_validate(m) for m in restore_messages]
        )
        engine_obj.load_messages(restored)
        if hasattr(agent_backend, "load_history"):
            agent_backend.load_history(restored)

    app_state = AppStateStore(
        AppState(
            model=model_name,
            cwd=cwd_resolved,
            permission_mode="default",
            theme="default",
            provider="javis",
            auth_status="ok",
            effort="medium",
            passes=1,
            output_style="default",
        )
    )

    return RuntimeBundle(
        engine=engine_obj,
        cwd=cwd_resolved,
        app_state=app_state,
        commands=create_default_command_registry(),
        session_backend=session_backend or JavisSessionBackend(workspace_root),
        session_id=session_id,
        system_prompt=system_prompt_text,
    )


async def start_runtime(bundle: RuntimeBundle) -> None:
    """Lifecycle hook — currently a no-op (no hooks/MCP/sandbox to start)."""
    return None


async def close_runtime(bundle: RuntimeBundle) -> None:
    """Lifecycle hook — currently a no-op (no resources to close)."""
    return None


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
        if result.continue_pending:
            async for event in bundle.engine.continue_pending(max_turns=result.continue_turns):
                await render_event(event)
            _save_session(bundle)
        return not result.should_exit

    # Normal prompt — feed it to the engine.
    async for event in bundle.engine.submit_message(user_message or line):
        await render_event(event)
    _save_session(bundle)
    return True


async def _replay_assistant(message: ConversationMessage):
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
    engine: str | None = None,
) -> int:
    """Run a single prompt and print the assistant output to stdout."""
    cwd_path = str(Path(cwd or Path.cwd()).resolve())
    previous_cwd = Path.cwd()
    import os
    os.chdir(cwd_path)
    try:
        bundle = await build_javis_runtime(
            cwd=cwd_path,
            model=model,
            max_turns=max_turns,
            engine=engine,
            workspace=workspace,
        )
        await start_runtime(bundle)

        async def _print_system(message: str) -> None:
            print(message, file=sys.stderr)

        saw_error = False

        async def _render_event(event) -> None:
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
        )
        await close_runtime(bundle)
        return 1 if saw_error else 0
    finally:
        os.chdir(previous_cwd)


__all__ = [
    "RuntimeBundle",
    "build_javis_backend_command",
    "build_javis_runtime",
    "close_runtime",
    "handle_line",
    "run_javis_print_mode",
    "start_runtime",
]
