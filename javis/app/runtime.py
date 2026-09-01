"""Runtime assembly and request dispatch for javis.

What remains:

- ``RuntimeBundle`` — engine + commands + app_state + session_backend
- ``build_runtime`` — assembles a bundle with an ``AgentEngine``
- ``handle_line`` — the single dispatch point (slash commands + agent turns)

Plugin wiring lives in ``build_runtime``: a fresh Cordis context provides the
built-in services (``config`` / ``tools`` / ``commands`` / ``host``), mounts
the plugin composition via the Cordis loader, and picks the engine from the
``engine`` service when a plugin provided one (falling back to the built-in
``HarnessEngine`` otherwise).

``handle_line`` yields ``AgentEvent`` straight through to the host's
``render_event`` callback — no ``StreamEvent`` translation layer.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from javis.commands.registry import CommandContext, CommandRegistry, create_default_command_registry
from javis.contracts.engine import AgentEngine
from javis.contracts.host import HostContext
from javis.contracts.messages import ConversationMessage, sanitize_conversation_messages
from javis.contracts.services import (
    COMMANDS_SERVICE,
    CONFIG_SERVICE,
    ENGINE_SERVICE,
    HOST_SERVICE,
    TOOLS_SERVICE,
)
from javis.contracts.types import AgentEvent, AgentTextDelta, AgentTurnEnd
from javis.cordis import Context
from javis.cordis.loader import Loader
from javis.cordis.registry import settle
from javis.session.config import JavisConfig, ensure_default_composition
from javis.session.session_storage import JavisSessionBackend
from javis.session.state import AppState, AppStateStore
from javis.session.workspace import initialize_workspace

log = logging.getLogger(__name__)

SystemPrinter = Callable[[str], Awaitable[None]]
StreamRenderer = Callable[[AgentEvent], Awaitable[None]]
ClearHandler = Callable[[], Awaitable[None]]


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def build_system_prompt(cwd: str | Path | None = None, *, workspace: str | Path | None = None) -> str:
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
    context: Context | None = None

    async def close(self) -> None:
        """Dispose every plugin fiber: effects unwind, provided services revoke.

        Idempotent — after the first call the registry no longer tracks
        fibers. Teardown errors are logged, never raised.
        """
        if self.context is None:
            return
        fibers = [
            fiber
            for runtime in list(self.context.registry.values())
            for fiber in list(runtime.fibers)
        ]
        disposals: list[Any] = []
        for fiber in reversed(fibers):
            try:
                result = fiber.dispose()
                if result is not None:
                    disposals.append(result)
            except BaseException as exc:  # noqa: BLE001 — teardown must not raise
                log.warning("error disposing plugin fiber %s: %s", fiber.name, exc)
        if disposals:
            await asyncio.gather(*disposals, return_exceptions=True)


def _build_default_engine(
    *,
    cfg: JavisConfig,
    model: str | None,
    system_prompt: str,
    cwd: str,
    max_turns: int | None,
    tool_metadata: dict[str, Any],
    workspace: str | Path,
    javis_tools: Any = None,
) -> AgentEngine:
    """Construct the built-in ``HarnessEngine`` from resolved config.

    This is the single seam where the engine is chosen: the runtime no longer
    accepts an injected engine, and future engine implementations (e.g. the
    plugin system's ``ctx.provide("engine", impl)``) replace the body of this
    function instead of threading an engine parameter through the runtime.

    ``javis_tools`` is the runtime's plugin-populated javis tool registry; the
    harness engine adapts it (built-ins + plugin tools) into its own registry.
    """
    from javis.engines.harness import build
    from javis.session.config import resolve_provider_and_model
    from javis.session.credentials import resolve_api_key

    provider_name, model_id = resolve_provider_and_model(cfg, cli_model=model)
    provider_cfg = cfg.providers[provider_name]
    api_key = resolve_api_key(
        provider_name,
        provider_cfg.api_key_env,
        provider_cfg.api_key,
        workspace=workspace,
        cwd=cwd,
    )
    max_tokens: int | None = None
    for m in provider_cfg.models:
        if m.id == model_id:
            max_tokens = m.max_tokens
            break
    if max_turns is None and cfg.session.max_turns is not None:
        max_turns = cfg.session.max_turns
    return build(
        model=model_id,
        api_key=api_key or "",
        base_url=provider_cfg.base_url,
        provider_name=provider_name,
        max_tokens=max_tokens,
        system_prompt=system_prompt,
        cwd=cwd,
        workspace=workspace,
        max_turns=max_turns,
        tool_metadata=tool_metadata,
        javis_tools=javis_tools,
    )


async def build_runtime(
    *,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    system_prompt: str | None = None,
    restore_messages: list[dict[str, Any]] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
    session_backend: JavisSessionBackend | None = None,
    workspace: str | Path | None = None,
    plugins: str | Path | None = None,
) -> RuntimeBundle:
    """Assemble a ``RuntimeBundle`` backed by an ``AgentEngine``.

    Plugin wiring: a fresh Cordis context provides the built-in services
    (``config`` / ``tools`` / ``commands`` / ``host``), mounts the plugin
    composition — CLI ``--plugins`` > ``JAVIS_PLUGINS`` > ``pluginsFile`` >
    ``<workspace>/cordis.yml`` — and waits for every fiber to settle. A
    plugin that provided ``engine`` supplies the engine object; otherwise
    ``_build_default_engine`` builds the built-in ``HarnessEngine`` (the
    seam tests patch with a fake).
    """
    cwd_resolved = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
    workspace_root = initialize_workspace(workspace)
    system_prompt_text = system_prompt or build_system_prompt(cwd_resolved, workspace=workspace_root)

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

    composition = _resolve_composition_path(
        cfg=cfg,
        workspace_root=workspace_root,
        plugins=plugins,
        cwd=cwd_resolved,
    )

    ctx = Context()
    ctx.baseUrl = str(workspace_root)
    # Built-in services: owner is the root fiber, so they are never revoked.
    ctx.provide(CONFIG_SERVICE, cfg)
    from javis.engines.tools import create_default_tool_registry

    tools_registry = create_default_tool_registry()
    ctx.provide(TOOLS_SERVICE, tools_registry)
    ctx.provide(COMMANDS_SERVICE, commands)
    ctx.provide(
        HOST_SERVICE,
        HostContext(
            cwd=cwd_resolved,
            workspace=str(workspace_root),
            session_id=session_id,
            tool_metadata=tool_metadata,
            model_override=model,
            max_turns_override=max_turns,
            system_prompt=system_prompt_text,
        ),
    )

    loader_fiber = ctx.plugin(Loader, {"file": str(composition)})
    try:
        await loader_fiber
        await settle(ctx)
    except BaseException:
        log.exception("Plugin composition %s failed to load", composition)
        raise

    engine_obj = ctx.get(ENGINE_SERVICE)
    if engine_obj is None or not isinstance(engine_obj, AgentEngine):
        if engine_obj is not None:
            log.warning(
                "engine service from plugin is not an AgentEngine (%s); "
                "falling back to the built-in engine",
                type(engine_obj).__name__,
            )
        engine_obj = _build_default_engine(
            cfg=cfg,
            model=model,
            system_prompt=system_prompt_text,
            cwd=cwd_resolved,
            max_turns=max_turns,
            tool_metadata=tool_metadata,
            workspace=workspace_root,
            javis_tools=tools_registry,
        )
    # Explicit CLI overrides win over the engine's resolved defaults.
    if model is not None:
        engine_obj.set_model(model)
    if system_prompt is not None:
        engine_obj.set_system_prompt(system_prompt)

    model_name = model or engine_obj.model or "unknown"

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
        context=ctx,
    )


def _resolve_composition_path(
    *,
    cfg: JavisConfig,
    workspace_root: str | Path,
    plugins: str | Path | None,
    cwd: str,
) -> Path:
    """Resolve the plugin composition file: CLI > env > config > default.

    CLI-supplied paths resolve against ``cwd``; env/config values against the
    workspace root; the default is ``<workspace>/cordis.yml`` (created when
    missing). An explicitly referenced but missing file is a hard error.
    """
    explicit: str | Path | None = plugins
    base = Path(cwd)
    if explicit is None:
        explicit = os.environ.get("JAVIS_PLUGINS")
        base = Path(workspace_root)
    if not explicit:
        explicit = cfg.plugins_file
        base = Path(workspace_root)
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = base / path
        if not path.exists():
            raise ValueError(f"Plugin composition file not found: {path}")
        return path.resolve()
    return ensure_default_composition(workspace_root)


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


__all__ = [
    "RuntimeBundle",
    "build_runtime",
    "build_system_prompt",
    "handle_line",
]
