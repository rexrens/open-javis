


import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from javis.app.wire import BackendEvent, FrontendRequest, TranscriptItem
from javis.commands.registry import CommandRegistry, create_default_command_registry
from javis.session.state import AppState, AppStateStore


class TUIBackendHost:

    _PROTOCOL_PREFIX = "OHJSON:"

    def __init__(self, *,
        cwd: str | Path | None = None,
        workspace: str | Path | None = None,
        model: str | None = None, max_turns: int | None = None, restore_messages: list[dict[str, Any]] | None = None, restore_tool_metadata: dict[str, object] | None = None, plugins: str | Path | None = None):
        self._running = True
        self._write_lock = asyncio.Lock()
        self._request_queue: asyncio.Queue[FrontendRequest] = asyncio.Queue()

        cwd_resolved = str(Path(cwd or ".").resolve())

        self._app_state: AppStateStore = AppStateStore(
            AppState(
                cwd=cwd_resolved,
                # workspace=workspace,
                model="model"
            )
        )
        self._commands: CommandRegistry = create_default_command_registry()

    async def _emit(self, event: BackendEvent) -> None:
        async with self._write_lock:
            payload = self._PROTOCOL_PREFIX + event.model_dump_json() + "\n"
            buffer = getattr(sys.stdout, "buffer", None)

            if buffer:
                buffer.write(payload.encode("utf-8"))
                buffer.flush()
            else:
                sys.stdout.write(payload)
                sys.stdout.flush()


    async def _read_request(self) -> None:
        while True:
            raw = await asyncio.to_thread(sys.stdin.buffer.readline)

            if not raw:
                # EOF 是关闭消息
                await self._request_queue.put(FrontendRequest(type="shutdown"))
                return

            #因为是JSONLINE，所以必然会读取到一行
            payload = raw.decode("utf-8").strip()

            if not payload or payload.startswith(self._PROTOCOL_PREFIX):
                continue

            try:
                request = FrontendRequest.model_validate_json(payload)
            except ValidationError as exc:
                await self._emit(
                    BackendEvent(
                        type="error",
                        message=f"Invalid request: {exc}",
                    )
                )
                continue

            await self._request_queue.put(request)


    async def _process_line(self, line: str) -> None:
        reply = f"assistant echo: {line}"
        
        await self._emit(BackendEvent(type="transcript_item", item=TranscriptItem(text=line, role="user")))
        for chunk in ("assistant ", "echo: ", line):
          await self._emit(BackendEvent(type="assistant_delta", message=chunk))
          await asyncio.sleep(0.02)

        await self._emit(BackendEvent(type="assistant_complete", message=reply))
        await self._emit(BackendEvent(type="line_complete"))
        await self._emit(BackendEvent(type="line_complete"))

    async def run(self) -> int:

        await self._emit(
            BackendEvent.ready(
                state=self._app_state.get(),
                commands=[f"/{command.name}" for command in self._commands.list_commands()],
            )
        )

        await self._emit(
            BackendEvent.status_snapshot(
                state=self._app_state.get(),
            )
        )

        reader = asyncio.create_task(self._read_request())

        try:

            while self._running:
                await asyncio.sleep(0.02)
                request = await self._request_queue.get()
                if request.type == "shutdown":
                    await self._emit(BackendEvent(type="shutdown"))
                    break

                line = (request.line or "").strip()
                try:
                    self._busy = False
                    await self._process_line(line)
                finally:
                    self._busy = True
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader

        return 0

    async def close(self):
        ...


async def run_tui_backend(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    restore_messages: list[dict[str, Any]] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
    plugins: str | Path | None = None,
) -> int:
    """Run the TUI backend"""

    host = TUIBackendHost(cwd=cwd)

    try:
        return await host.run()
    finally:
        await host.close()
