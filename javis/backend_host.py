"""Backend host for javis — ``ReactBackendHost`` with a pre-built bundle.

``ReactBackendHost.run()`` calls ``build_runtime()`` internally, which creates
a real ``QueryEngine``. javis needs to inject a ``MockEngine`` instead, so we
subclass and override ``run()`` to consume a pre-built ``RuntimeBundle``.

Everything else (modal futures, select_command, emit, request dispatch) is
inherited unchanged — javis gets the full selector coverage for free.
"""

from __future__ import annotations

import asyncio
import contextlib

from openharness.tasks import get_task_manager
from openharness.ui.backend_host import BackendHostConfig, ReactBackendHost
from openharness.ui.protocol import BackendEvent
from openharness.ui.runtime import RuntimeBundle, close_runtime, start_runtime


class JavisBackendHost(ReactBackendHost):
    """``ReactBackendHost`` that skips ``build_runtime`` and uses a pre-built bundle."""

    def __init__(self, bundle: RuntimeBundle, config: BackendHostConfig) -> None:
        super().__init__(config)
        # Set _bundle immediately so tests can use host._bundle without calling run().
        # run() will skip the build_runtime() call that the parent would have made.
        self._bundle = bundle

    async def run(self) -> int:
        """Same loop as ``ReactBackendHost.run()`` but with a pre-built bundle."""
        await start_runtime(self._bundle)
        await self._emit(
            BackendEvent.ready(
                self._bundle.app_state.get(),
                get_task_manager().list_tasks(),
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
            if self._bundle is not None:
                await close_runtime(self._bundle)
        return 0


__all__ = ["JavisBackendHost"]
