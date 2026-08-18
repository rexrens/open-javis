"""JSON-lines backend host for the React terminal frontend.

Forked from openharness.ui.backend_host and trimmed:
- No ``get_task_manager()`` / ``get_bridge_manager()`` — tasks and bridge
  snapshots are always empty.
- No ``is_coordinator_mode()`` / ``drain_coordinator_async_agents()``.
- No TodoWrite / plan_mode tool special-casing (those are openharness tools).
- ``_render_event`` consumes ``AgentEvent`` directly — no ``StreamEvent`` layer.
- ``_handle_select_command`` keeps the selectors that don't need external
  subsystems (model, permissions, turns, theme, fast, vim, voice). Provider,
  output-style, effort, passes and resume are dropped.

The wire format (``OHJSON:`` prefix + JSON) and the modal/select future dance
are preserved unchanged so the React frontend works without modification.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Coroutine
from uuid import uuid4

from javis.core.messages import ConversationMessage, ImageBlock, TextBlock
from javis.host.wire import BackendEvent, FrontendImageAttachment, FrontendRequest, TranscriptItem
from javis.host.runtime import RuntimeBundle, close_runtime, handle_line, start_runtime

log = logging.getLogger(__name__)

_PROTOCOL_PREFIX = "OHJSON:"


@dataclass(frozen=True)
class BackendHostConfig:
    """Configuration for one backend host session."""

    model: str | None = None
    max_turns: int | None = None
    cwd: str | None = None
    workspace: str | Path | None = None


class JavisBackendHost:
    """Drive the javis runtime over a structured stdin/stdout protocol."""

    def __init__(self, bundle: RuntimeBundle, config: BackendHostConfig) -> None:
        self._bundle = bundle
        self._config = config
        self._write_lock = asyncio.Lock()
        self._request_queue: asyncio.Queue[FrontendRequest] = asyncio.Queue()
        self._permission_requests: dict[str, asyncio.Future[bool]] = {}
        self._edit_approval_requests: dict[str, asyncio.Future[str]] = {}
        self._question_requests: dict[str, asyncio.Future[str]] = {}
        self._permission_lock = asyncio.Lock()
        self._busy = False
        self._running = True
        self._active_request_task: asyncio.Task[bool] | None = None
        self._last_tool_inputs: dict[str, dict] = {}
        self._edit_always_approved = False

    async def run(self) -> int:
        """Main loop: emit ready, then read requests and dispatch."""
        await start_runtime(self._bundle)
        await self._emit(
            BackendEvent.ready(
                self._bundle.app_state.get(),
                [f"/{command.name}" for command in self._bundle.commands.list_commands()],
            )
        )
        await self._emit(self._status_snapshot())

        reader = asyncio.create_task(self._read_requests())
        try:
            while self._running:
                request = await self._request_queue.get()
                if request.type == "shutdown":
                    await self._emit(BackendEvent(type="shutdown"))
                    break
                if request.type == "interrupt":
                    await self._interrupt_active_request()
                    continue
                if request.type in ("permission_response", "question_response"):
                    continue
                if request.type == "list_sessions":
                    await self._handle_list_sessions()
                    continue
                if request.type == "select_command":
                    await self._handle_select_command(request.command or "")
                    continue
                if request.type == "apply_select_command":
                    if self._busy:
                        await self._emit(BackendEvent(type="error", message="Session is busy"))
                        continue
                    self._busy = True
                    try:
                        should_continue = await self._run_active_request(
                            self._apply_select_command(
                                request.command or "",
                                request.value or "",
                            )
                        )
                    finally:
                        self._busy = False
                    if not should_continue:
                        await self._emit(BackendEvent(type="shutdown"))
                        break
                    continue
                if request.type != "submit_line":
                    await self._emit(BackendEvent(type="error", message=f"Unknown request type: {request.type}"))
                    continue
                if self._busy:
                    await self._emit(BackendEvent(type="error", message="Session is busy"))
                    continue
                line = (request.line or "").strip()
                if not line and not request.images:
                    continue
                self._busy = True
                try:
                    should_continue = await self._run_active_request(
                        self._process_line(line, images=request.images)
                    )
                finally:
                    self._busy = False
                if not should_continue:
                    await self._emit(BackendEvent(type="shutdown"))
                    break
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
            await close_runtime(self._bundle)
        return 0

    async def _read_requests(self) -> None:
        while True:
            raw = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not raw:
                await self._request_queue.put(FrontendRequest(type="shutdown"))
                return
            payload = raw.decode("utf-8").strip()
            if not payload:
                continue
            try:
                request = FrontendRequest.model_validate_json(payload)
            except Exception as exc:
                await self._emit(BackendEvent(type="error", message=f"Invalid request: {exc}"))
                continue
            if request.type == "permission_response" and request.request_id in self._edit_approval_requests:
                future = self._edit_approval_requests[request.request_id]
                if not future.done():
                    future.set_result(_edit_approval_reply_from_request(request))
                continue
            if request.type == "permission_response" and request.request_id in self._permission_requests:
                future = self._permission_requests[request.request_id]
                if not future.done():
                    future.set_result(bool(request.allowed))
                continue
            if request.type == "question_response" and request.request_id in self._question_requests:
                future = self._question_requests[request.request_id]
                if not future.done():
                    future.set_result(request.answer or "")
                continue
            if request.type == "interrupt":
                await self._interrupt_active_request()
                continue
            await self._request_queue.put(request)

    async def _run_active_request(self, awaitable: Coroutine[Any, Any, bool]) -> bool:
        task = asyncio.create_task(awaitable)
        self._active_request_task = task
        try:
            return await task
        except asyncio.CancelledError:
            await self._emit(
                BackendEvent(
                    type="transcript_item",
                    item=TranscriptItem(role="system", text="Interrupted by user."),
                )
            )
            await self._emit(self._status_snapshot())
            await self._emit(BackendEvent.tasks_snapshot())
            await self._emit(BackendEvent(type="line_complete"))
            return True
        finally:
            if self._active_request_task is task:
                self._active_request_task = None

    async def _interrupt_active_request(self) -> None:
        task = self._active_request_task
        if task is None or task.done():
            return
        task.cancel()

    async def _process_line(
        self,
        line: str,
        *,
        transcript_line: str | None = None,
        images: list[FrontendImageAttachment] | None = None,
    ) -> bool:
        user_message = _build_user_message_with_images(line, images or [])
        await self._emit(
            BackendEvent(
                type="transcript_item",
                item=TranscriptItem(
                    role="user",
                    text=transcript_line or _format_transcript_line(line, images or []),
                ),
            )
        )

        async def _print_system(message: str) -> None:
            await self._emit(
                BackendEvent(type="transcript_item", item=TranscriptItem(role="system", text=message))
            )

        async def _render_event(event) -> None:
            if isinstance(event, AgentTextDelta):
                await self._emit(BackendEvent(type="assistant_delta", message=event.text))
                return
            if isinstance(event, AgentToolCallStart):
                self._last_tool_inputs[event.tool_name] = event.tool_input or {}
                await self._emit(
                    BackendEvent(
                        type="tool_started",
                        tool_name=event.tool_name,
                        tool_input=event.tool_input,
                        item=TranscriptItem(
                            role="tool",
                            text=f"{event.tool_name} {json.dumps(event.tool_input, ensure_ascii=True)}",
                            tool_name=event.tool_name,
                            tool_input=event.tool_input,
                        ),
                    )
                )
                return
            if isinstance(event, AgentToolCallResult):
                await self._emit(
                    BackendEvent(
                        type="tool_completed",
                        tool_name=event.tool_name,
                        output=event.output,
                        is_error=event.is_error,
                        item=TranscriptItem(
                            role="tool_result",
                            text=event.output,
                            tool_name=event.tool_name,
                            is_error=event.is_error,
                        ),
                    )
                )
                await self._emit(self._status_snapshot())
                return
            if isinstance(event, AgentError):
                await self._emit(BackendEvent(type="error", message=event.message))
                await self._emit(
                    BackendEvent(type="transcript_item", item=TranscriptItem(role="system", text=event.message))
                )
                return
            if isinstance(event, AgentStatus):
                await self._emit(
                    BackendEvent(type="transcript_item", item=TranscriptItem(role="system", text=event.message))
                )
                return
            if isinstance(event, AgentTurnEnd):
                text = event.text
                await self._emit(
                    BackendEvent(
                        type="assistant_complete",
                        message=text,
                        item=TranscriptItem(role="assistant", text=text),
                    )
                )
                return

        async def _clear_output() -> None:
            await self._emit(BackendEvent(type="clear_transcript"))

        handle_line_kwargs: dict[str, Any] = {
            "print_system": _print_system,
            "render_event": _render_event,
            "clear_output": _clear_output,
        }
        if user_message is not None:
            handle_line_kwargs["user_message"] = user_message
        should_continue = await handle_line(self._bundle, line, **handle_line_kwargs)
        await self._emit(self._status_snapshot())
        await self._emit(BackendEvent.tasks_snapshot())
        await self._emit(BackendEvent(type="line_complete"))
        return should_continue

    async def _apply_select_command(self, command_name: str, value: str) -> bool:
        command = command_name.strip().lstrip("/").lower()
        selected = value.strip()
        line = self._build_select_command_line(command, selected)
        if line is None:
            await self._emit(BackendEvent(type="error", message=f"Unknown select command: {command_name}"))
            await self._emit(BackendEvent(type="line_complete"))
            return True
        return await self._process_line(line, transcript_line=f"/{command}")

    def _build_select_command_line(self, command: str, value: str) -> str | None:
        if command == "permissions":
            return f"/permissions {value}"
        if command == "theme":
            return f"/theme {value}"
        if command == "turns":
            return f"/turns {value}"
        if command == "fast":
            return f"/fast {value}"
        if command == "vim":
            return f"/vim {value}"
        if command == "voice":
            return f"/voice {value}"
        if command == "model":
            return f"/model {value}"
        return None

    def _status_snapshot(self) -> BackendEvent:
        return BackendEvent.status_snapshot(self._bundle.app_state.get())

    async def _handle_list_sessions(self) -> None:
        sessions = self._bundle.session_backend.list_snapshots(self._bundle.cwd, limit=10)
        options = []
        import time as _time
        for s in sessions:
            ts = _time.strftime("%m/%d %H:%M", _time.localtime(s["created_at"]))
            summary = s.get("summary", "")[:50] or "(no summary)"
            options.append({
                "value": s["session_id"],
                "label": f"{ts}  {s['message_count']}msg  {summary}",
            })
        await self._emit(
            BackendEvent(
                type="select_request",
                modal={"kind": "select", "title": "Resume Session", "command": "resume"},
                select_options=options,
            )
        )

    async def _handle_select_command(self, command_name: str) -> None:
        command = command_name.strip().lstrip("/").lower()
        state = self._bundle.app_state.get()

        if command == "permissions":
            options = [
                {"value": "default", "label": "Default", "description": "Ask before write/execute", "active": state.permission_mode == "default"},
                {"value": "full_auto", "label": "Auto", "description": "Allow all tools automatically", "active": state.permission_mode == "full_auto"},
                {"value": "plan", "label": "Plan Mode", "description": "Block all write operations", "active": state.permission_mode == "plan"},
            ]
            await self._emit(BackendEvent(type="select_request", modal={"kind": "select", "title": "Permission Mode", "command": "permissions"}, select_options=options))
            return

        if command == "theme":
            themes = ["default", "dark", "light", "solarized", "gruvbox"]
            options = [{"value": name, "label": name, "active": name == state.theme} for name in themes]
            await self._emit(BackendEvent(type="select_request", modal={"kind": "select", "title": "Theme", "command": "theme"}, select_options=options))
            return

        if command == "turns":
            current = self._bundle.engine.max_turns
            values = {32, 64, 128, 200, 256, 512}
            if isinstance(current, int):
                values.add(current)
            options = [{"value": "unlimited", "label": "Unlimited", "description": "Do not hard-stop", "active": current is None}]
            options.extend({"value": str(v), "label": f"{v} turns", "active": v == current} for v in sorted(values))
            await self._emit(BackendEvent(type="select_request", modal={"kind": "select", "title": "Max Turns", "command": "turns"}, select_options=options))
            return

        if command == "fast":
            current = bool(state.fast_mode)
            options = [
                {"value": "on", "label": "On", "description": "Prefer shorter responses", "active": current},
                {"value": "off", "label": "Off", "description": "Normal response mode", "active": not current},
            ]
            await self._emit(BackendEvent(type="select_request", modal={"kind": "select", "title": "Fast Mode", "command": "fast"}, select_options=options))
            return

        if command == "vim":
            current = bool(state.vim_enabled)
            options = [
                {"value": "on", "label": "On", "description": "Enable Vim keybindings", "active": current},
                {"value": "off", "label": "Off", "description": "Standard keybindings", "active": not current},
            ]
            await self._emit(BackendEvent(type="select_request", modal={"kind": "select", "title": "Vim Mode", "command": "vim"}, select_options=options))
            return

        if command == "voice":
            current = bool(state.voice_enabled)
            options = [
                {"value": "on", "label": "On", "description": "Enable voice mode", "active": current},
                {"value": "off", "label": "Off", "description": "Disable voice mode", "active": not current},
            ]
            await self._emit(BackendEvent(type="select_request", modal={"kind": "select", "title": "Voice Mode", "command": "voice"}, select_options=options))
            return

        if command == "model":
            current = state.model
            candidates = [current] if current else []
            seen: set[str] = set()
            options = []
            for value in candidates:
                if not value or value in seen:
                    continue
                seen.add(value)
                options.append({"value": value, "label": value, "description": "javis model", "active": value == current})
            await self._emit(BackendEvent(type="select_request", modal={"kind": "select", "title": "Model", "command": "model"}, select_options=options))
            return

        await self._emit(BackendEvent(type="error", message=f"No selector available for /{command}"))

    async def _ask_permission(self, tool_name: str, reason: str) -> bool:
        async with self._permission_lock:
            request_id = uuid4().hex
            future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            self._permission_requests[request_id] = future
            await self._emit(
                BackendEvent(
                    type="modal_request",
                    modal={"kind": "permission", "request_id": request_id, "tool_name": tool_name, "reason": reason},
                )
            )
            try:
                return await asyncio.wait_for(future, timeout=300)
            except asyncio.TimeoutError:
                log.warning("Permission request %s timed out after 300s, denying", request_id)
                return False
            finally:
                self._permission_requests.pop(request_id, None)

    async def _ask_edit_approval(self, path: str, diff: str, added: int, removed: int) -> str:
        if self._edit_always_approved or self._bundle.app_state.get().permission_mode == "full_auto":
            return "always"
        async with self._permission_lock:
            request_id = uuid4().hex
            future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            self._edit_approval_requests[request_id] = future
            await self._emit(
                BackendEvent(
                    type="modal_request",
                    modal={"kind": "edit_diff", "request_id": request_id, "path": path, "diff": diff, "added": added, "removed": removed},
                )
            )
            try:
                reply = await asyncio.wait_for(future, timeout=300)
            except asyncio.TimeoutError:
                log.warning("Edit approval request %s timed out after 300s, denying", request_id)
                reply = "reject"
            finally:
                self._edit_approval_requests.pop(request_id, None)
                await self._emit(BackendEvent(type="modal_request", modal=None))
            if reply == "always":
                self._edit_always_approved = True
            return reply

    async def _ask_question(self, question: str) -> str:
        request_id = uuid4().hex
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._question_requests[request_id] = future
        await self._emit(
            BackendEvent(
                type="modal_request",
                modal={"kind": "question", "request_id": request_id, "question": question},
            )
        )
        try:
            return await future
        finally:
            self._question_requests.pop(request_id, None)

    async def _emit(self, event: BackendEvent) -> None:
        async with self._write_lock:
            payload = _PROTOCOL_PREFIX + event.model_dump_json() + "\n"
            buffer = getattr(sys.stdout, "buffer", None)
            if buffer is not None:
                buffer.write(payload.encode("utf-8"))
                buffer.flush()
                return
            sys.stdout.write(payload)
            sys.stdout.flush()


def _build_user_message_with_images(
    line: str,
    images: list[FrontendImageAttachment],
) -> ConversationMessage | None:
    if not images:
        return None
    content = [TextBlock(text=line or "Please analyze the attached image.")]
    content.extend(
        ImageBlock(
            media_type=image.media_type,
            data=image.data,
            source_path=image.source_path or "",
        )
        for image in images
    )
    return ConversationMessage.from_user_content(content)


def _format_transcript_line(line: str, images: list[FrontendImageAttachment]) -> str:
    if not images:
        return line
    noun = "image" if len(images) == 1 else "images"
    attachment_line = f"[{len(images)} {noun} attached]"
    return f"{line}\n{attachment_line}" if line else attachment_line


def _edit_approval_reply_from_request(request: FrontendRequest) -> str:
    reply = (request.permission_reply or "").strip().lower()
    if reply in {"once", "always", "reject"}:
        return reply
    return "once" if bool(request.allowed) else "reject"


async def run_javis_backend(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    engine: str | None = None,
    restore_messages: list[dict] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
) -> int:
    """Run the structured React backend host."""
    import os
    if cwd:
        os.chdir(cwd)
    bundle = await build_javis_runtime(
        cwd=cwd,
        model=model,
        max_turns=max_turns,
        engine=engine,
        restore_messages=restore_messages,
        restore_tool_metadata=restore_tool_metadata,
        workspace=workspace,
    )
    host = JavisBackendHost(
        bundle=bundle,
        config=BackendHostConfig(model=model, max_turns=max_turns, cwd=cwd, workspace=workspace),
    )
    return await host.run()


# Late imports — keep these at the bottom to avoid circular dependencies.
from javis.core.types import (  # noqa: E402
    AgentError,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.host.runtime import build_javis_runtime  # noqa: E402


__all__ = [
    "BackendHostConfig",
    "JavisBackendHost",
    "run_javis_backend",
]
