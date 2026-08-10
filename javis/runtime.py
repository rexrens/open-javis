"""Runtime assembly for javis — a slim alternative to ``openharness.ui.runtime.build_runtime``.

Skips MCP connection, hook loading, docker sandbox, autodream and session
memory — none of which a mock agent needs. The engine is a ``MockEngine``
backed by an ``AgentBackend`` (``MockAgent`` by default).

To swap in a real agent, implement ``AgentBackend`` and pass it to
``build_javis_runtime`` via ``agent_backend=`` — no other change is needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from openharness.api.client import SupportsStreamingMessages
from openharness.api.usage import UsageSnapshot
from openharness.bridge import get_bridge_manager
from openharness.commands import create_default_command_registry
from openharness.config.settings import load_settings
from openharness.engine.messages import ConversationMessage, sanitize_conversation_messages
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
)
from openharness.hooks import HookEvent, HookExecutionContext, HookExecutor
from openharness.hooks.loader import HookRegistry
from openharness.mcp.client import McpClientManager
from openharness.tools import create_default_tool_registry
from openharness.ui.backend_host import BackendHostConfig
from openharness.ui.react_launcher import _resolve_npm, _resolve_tsx, get_frontend_dir
from openharness.ui.runtime import RuntimeBundle, close_runtime, handle_line, start_runtime

from javis.engine.mock_agent import MockAgent
from javis.engine.mock_engine import MockEngine
from javis.engine.protocol import AgentBackend
from javis.prompts import build_javis_system_prompt
from javis.session_storage import JavisSessionBackend
from javis.workspace import initialize_workspace


class MockApiClient:
    """No-op ``SupportsStreamingMessages`` used to satisfy runtime typing.

    ``MockEngine`` never calls ``stream_message`` — the mock agent produces
    events directly. This class exists so ``RuntimeBundle.api_client`` and
    ``HookExecutionContext.api_client`` have a value to hold.
    """

    async def stream_message(self, request):  # pragma: no cover - never called
        del request
        return
        yield  # make this an async generator for typing purposes

    async def close(self) -> None:
        return None


def _resolve_model(model: str | None) -> str:
    return model or "javis-mock"


async def build_javis_runtime(
    *,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    system_prompt: str | None = None,
    agent_backend: AgentBackend | None = None,
    restore_messages: list[dict] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
    session_backend: JavisSessionBackend | None = None,
    workspace: str | Path | None = None,
    extra_skill_dirs: Iterable[str | Path] = (),
    extra_plugin_roots: Iterable[str | Path] = (),
) -> RuntimeBundle:
    """Assemble a ``RuntimeBundle`` with a ``MockEngine`` instead of ``QueryEngine``.

    Mirrors the shape of ``openharness.ui.runtime.build_runtime`` but drops
    model-client resolution, MCP connect, hook loading, sandbox, autodream and
    session memory. The engine is a ``MockEngine`` over the supplied
    ``agent_backend`` (default ``MockAgent()``).
    """
    settings = load_settings()
    cwd_resolved = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
    workspace_root = initialize_workspace(workspace)
    model_name = _resolve_model(model)
    system_prompt_text = system_prompt or build_javis_system_prompt(cwd_resolved, workspace=workspace_root)

    # MCP manager constructed but not connected — mock agent doesn't use MCP.
    mcp_manager = McpClientManager({})
    tool_registry = create_default_tool_registry(mcp_manager)

    # Empty hook registry so SESSION_START / SESSION_END are no-ops.
    api_client = MockApiClient()
    hook_executor = HookExecutor(
        HookRegistry(),
        HookExecutionContext(
            cwd=Path(cwd_resolved),
            api_client=api_client,  # type: ignore[arg-type]
            default_model=model_name,
        ),
    )

    # App state — minimal fields filled so the React status bar renders.
    from openharness.state import AppState, AppStateStore

    app_state = AppStateStore(
        AppState(
            model=model_name,
            permission_mode=settings.permission.mode.value,
            theme=settings.theme,
            cwd=cwd_resolved,
            provider="javis",
            auth_status="ok",
            base_url="",
            effort=settings.effort,
            passes=settings.passes,
            output_style=settings.output_style,
        )
    )

    # MockEngine — the heart of the swap.
    agent = agent_backend or MockAgent()
    tool_metadata: dict[str, Any] = {
        "permission_mode": settings.permission.mode.value,
        "read_file_state": [],
        "invoked_skills": [],
        "async_agent_state": [],
        "async_agent_tasks": [],
        "recent_work_log": [],
        "recent_verified_work": [],
        "task_focus_state": {
            "goal": "",
            "recent_goals": [],
            "active_artifacts": [],
            "verified_state": [],
            "next_step": "",
        },
        "compact_checkpoints": [],
    }
    if isinstance(restore_tool_metadata, dict):
        tool_metadata.update(restore_tool_metadata)

    session_id = uuid4().hex[:12]
    tool_metadata["session_id"] = session_id

    engine = MockEngine(
        agent_backend=agent,
        model=model_name,
        system_prompt=system_prompt_text,
        cwd=cwd_resolved,
        max_turns=max_turns,
        tool_metadata=tool_metadata,
        api_client=api_client,
    )

    if restore_messages:
        restored = sanitize_conversation_messages(
            [ConversationMessage.model_validate(m) for m in restore_messages]
        )
        engine.load_messages(restored)

    normalized_skill_dirs = tuple(str(Path(p).expanduser().resolve()) for p in extra_skill_dirs)
    normalized_plugin_roots = tuple(str(Path(p).expanduser().resolve()) for p in extra_plugin_roots)

    return RuntimeBundle(
        api_client=api_client,  # type: ignore[arg-type]
        cwd=cwd_resolved,
        mcp_manager=mcp_manager,
        tool_registry=tool_registry,
        app_state=app_state,
        hook_executor=hook_executor,
        engine=engine,  # type: ignore[arg-type]
        commands=create_default_command_registry(),
        external_api_client=True,
        enforce_max_turns=False,
        session_id=session_id,
        session_backend=session_backend or JavisSessionBackend(workspace_root),
        extra_skill_dirs=normalized_skill_dirs,
        extra_plugin_roots=normalized_plugin_roots,
    )


def build_javis_backend_command(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
) -> list[str]:
    """Return the backend command the React frontend will spawn."""
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


async def run_javis_backend(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    restore_messages: list[dict] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
) -> int:
    """Run the structured React backend host with javis's MockEngine."""
    from javis.backend_host import JavisBackendHost

    cwd_path = str(Path(cwd or Path.cwd()).resolve())
    workspace_root = initialize_workspace(workspace)
    bundle = await build_javis_runtime(
        cwd=cwd_path,
        model=model,
        max_turns=max_turns,
        restore_messages=restore_messages,
        restore_tool_metadata=restore_tool_metadata,
        workspace=workspace_root,
    )
    host = JavisBackendHost(
        bundle=bundle,
        config=BackendHostConfig(
            model=model,
            max_turns=max_turns,
            cwd=cwd_path,
        ),
    )
    return await host.run()


async def launch_javis_tui(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
) -> int:
    """Launch the React terminal frontend with a javis backend.

    Mirrors ``openharness.ui.react_launcher.launch_react_tui`` but spawns
    ``python -m javis --backend-only`` instead of ``python -m openharness``.
    """
    frontend_dir = get_frontend_dir()
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        raise RuntimeError(f"React terminal frontend is missing: {package_json}")

    npm = _resolve_npm()
    if not (frontend_dir / "node_modules").exists():
        install = await asyncio.create_subprocess_exec(
            npm,
            "install",
            "--no-fund",
            "--no-audit",
            cwd=str(frontend_dir),
        )
        if await install.wait() != 0:
            raise RuntimeError("Failed to install React terminal frontend dependencies")

    cwd_path = str(Path(cwd or Path.cwd()).resolve())
    workspace_root = initialize_workspace(workspace)
    env = os.environ.copy()
    env["OPENHARNESS_FRONTEND_CONFIG"] = json.dumps(
        {
            "backend_command": build_javis_backend_command(
                cwd=cwd_path,
                workspace=workspace_root,
                model=model,
                max_turns=max_turns,
            ),
            "initial_prompt": None,
            "theme": "default",
        }
    )
    tsx_cmd = _resolve_tsx(frontend_dir)
    process = await asyncio.create_subprocess_exec(
        *tsx_cmd,
        "src/index.tsx",
        cwd=str(frontend_dir),
        env=env,
        stdin=None,
        stdout=None,
        stderr=None,
    )
    return await process.wait()


async def run_javis_print_mode(
    *,
    prompt: str,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
) -> int:
    """Run a single javis prompt and print the assistant output."""
    cwd_path = str(Path(cwd or Path.cwd()).resolve())
    workspace_root = initialize_workspace(workspace)
    previous_cwd = Path.cwd()
    os.chdir(cwd_path)
    try:
        bundle = await build_javis_runtime(
            cwd=cwd_path,
            model=model,
            max_turns=max_turns,
            workspace=workspace_root,
        )
        await start_runtime(bundle)

        async def _print_system(message: str) -> None:
            print(message, file=sys.stderr)

        saw_error = False

        async def _render_event(event) -> None:
            nonlocal saw_error
            if isinstance(event, AssistantTextDelta):
                sys.stdout.write(event.text)
                sys.stdout.flush()
            elif isinstance(event, AssistantTurnComplete):
                sys.stdout.write("\n")
                sys.stdout.flush()
            elif isinstance(event, ErrorEvent):
                saw_error = True
                print(event.message, file=sys.stderr)
            elif isinstance(event, CompactProgressEvent):
                if event.message:
                    print(event.message, file=sys.stderr)
            elif isinstance(event, StatusEvent):
                print(event.message, file=sys.stderr)

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
    "MockApiClient",
    "build_javis_backend_command",
    "build_javis_runtime",
    "launch_javis_tui",
    "run_javis_backend",
    "run_javis_print_mode",
]
