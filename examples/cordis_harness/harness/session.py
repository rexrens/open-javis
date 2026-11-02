"""Durable sessions: an append-only JSONL event log (``ctx.sessions``).

The log is the source of the context the model sees: :meth:`Session.messages`
projects model history from it, so a resumed session continues exactly where
it stopped. One file per session under ``~/.cordis-harness/sessions``.

The service also owns the **durable firehose**: every committed event is
broadcast as ``session/event(session, event)``, so consumers (front ends,
projections, telemetry) read durable facts from one place instead of being
handed them by whoever wrote them.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from .composition import DEFAULT_HOME
from .types import ContentBlock, Message, assistant_message, now_iso, text_block, user_message

if TYPE_CHECKING:
    from javis.cordis import Context

#: Bumped when the on-disk event vocabulary changes incompatibly.
SESSION_VERSION = 1

#: Session logs live under the harness home, next to the user patch.
DEFAULT_SESSION_DIR = f"{DEFAULT_HOME}/sessions"

EVENT_TYPES = (
    "turn/start",
    "turn/end",
    "user/message",
    "assistant/message",
    "tool/call",
    "tool/result",
)


class SessionError(Exception):
    """A session could not be created, opened or read."""


# -- event constructors -----------------------------------------------------


def turn_start_event(index: int) -> dict[str, Any]:
    return {"type": "turn/start", "turn": index, "time": now_iso()}


def turn_end_event(index: int) -> dict[str, Any]:
    return {"type": "turn/end", "turn": index, "time": now_iso()}


def user_event(text: str) -> dict[str, Any]:
    return {"type": "user/message", "content": [text_block(text)], "time": now_iso()}


def assistant_event(
    blocks: list[ContentBlock],
    usage: dict[str, int] | None = None,
    finish: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {"type": "assistant/message", "content": blocks, "time": now_iso()}
    if usage:
        event["usage"] = usage
    if finish:
        event["finish"] = finish
    return event


def tool_call_event(call_id: str, name: str, arguments: str) -> dict[str, Any]:
    return {"type": "tool/call", "id": call_id, "name": name, "arguments": arguments, "time": now_iso()}


def tool_result_event(call_id: str, name: str, text: str, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "tool/result",
        "id": call_id,
        "name": name,
        "text": text,
        "isError": is_error,
        "time": now_iso(),
    }


# -- session ----------------------------------------------------------------


@dataclass
class SessionInfo:
    """Header facts plus the file's modification time."""

    id: str
    path: str
    created: str
    updated: str
    cwd: str
    provider: str
    model: str
    mtime: float = 0.0


class Session:
    """One append-only session log."""

    def __init__(
        self,
        path: Path,
        session_id: str,
        cwd: str,
        provider: str,
        model: str,
        created: str | None = None,
        emit: Callable[["Session", dict[str, Any]], None] | None = None,
    ):
        self.path = Path(path)
        self.id = session_id
        self.cwd = cwd
        self.provider = provider
        self.model = model
        self.created = created or now_iso()
        self._emit = emit
        self._turns = 0

    # -- lifecycle ----------------------------------------------------------

    @classmethod
    def create(
        cls,
        directory: Path,
        cwd: str,
        provider: str,
        model: str,
        emit: Callable[["Session", dict[str, Any]], None] | None = None,
    ) -> "Session":
        """Create a fresh session file with its header line."""
        directory = Path(directory).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        session = cls(directory / f"{session_id}.jsonl", session_id, cwd, provider, model, emit=emit)
        session._write(
            {
                "type": "session/header",
                "version": SESSION_VERSION,
                "id": session_id,
                "created": session.created,
                "cwd": cwd,
                "provider": provider,
                "model": model,
            }
        )
        return session

    @classmethod
    def open(
        cls,
        directory: Path,
        session_id: str,
        emit: Callable[["Session", dict[str, Any]], None] | None = None,
    ) -> "Session":
        """Open an existing session by id (raises :class:`SessionError`)."""
        path = Path(directory).expanduser() / f"{session_id}.jsonl"
        if not path.is_file():
            raise SessionError(f'no session "{session_id}" in {Path(directory).expanduser()}')
        header = cls._read_header(path)
        session = cls(
            path,
            header.get("id", session_id),
            header.get("cwd", "."),
            header.get("provider", ""),
            header.get("model", ""),
            header.get("created"),
            emit,
        )
        session._turns = sum(1 for event in session.events() if event.get("type") == "turn/start")
        return session

    @staticmethod
    def _read_header(path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                line = handle.readline()
        except OSError as error:
            raise SessionError(f"cannot read {path}: {error}") from error
        try:
            header = json.loads(line)
        except json.JSONDecodeError as error:
            raise SessionError(f"invalid session header in {path}: {error}") from error
        if not isinstance(header, dict) or header.get("type") != "session/header":
            raise SessionError(f"{path} is not a session log")
        version = header.get("version")
        if version != SESSION_VERSION:
            raise SessionError(f"unsupported session version {version!r} in {path}")
        return header

    # -- log ----------------------------------------------------------------

    def _write(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        """Commit one event, then broadcast it as ``session/event``."""
        if event.get("type") == "turn/start":
            self._turns += 1
        self._write(event)
        if self._emit is not None:
            self._emit(self, event)
        return event

    @property
    def turns(self) -> int:
        return self._turns

    def events(self) -> Iterator[dict[str, Any]]:
        """Every event after the header, in log order."""
        if not self.path.is_file():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            next(handle, None)  # header
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event

    # -- projection ---------------------------------------------------------

    def messages(self) -> list[Message]:
        """Model history derived from the log."""
        messages: list[Message] = []
        for event in self.events():
            kind = event.get("type")
            if kind == "user/message":
                messages.append(user_message(_event_text(event)))
            elif kind == "assistant/message":
                blocks = event.get("content") or []
                if blocks:
                    messages.append(assistant_message(blocks))
            elif kind == "tool/result":
                messages.append(
                    {
                        "role": "tool",
                        "content": [
                            {
                                "type": "tool-result",
                                "toolCallId": event.get("id", ""),
                                "content": [text_block(event.get("text", ""))],
                                "isError": bool(event.get("isError")),
                            }
                        ],
                    }
                )
        return messages

    def info(self) -> SessionInfo:
        try:
            updated = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.path.stat().st_mtime))
        except OSError:
            updated = self.created
        return SessionInfo(
            id=self.id,
            path=str(self.path),
            created=self.created,
            updated=updated,
            cwd=self.cwd,
            provider=self.provider,
            model=self.model,
        )

    def __repr__(self) -> str:
        return f"<Session {self.id} {'/'.join(filter(None, [self.provider, self.model]))}>"


def _event_text(event: dict[str, Any]) -> str:
    blocks = event.get("content") or []
    return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")


class SessionService:
    """Session creation, discovery and resume, installed as ``ctx.sessions``."""

    def __init__(self, ctx: "Context", directory: str | Path | None = None):
        self.ctx = ctx
        self.directory = Path(directory).expanduser() if directory else Path(DEFAULT_SESSION_DIR).expanduser()

    def _broadcast(self, session: Session, event: dict[str, Any]) -> None:
        if self.ctx is not None:
            self.ctx.emit("session/event", session, event)

    def create(self, cwd: str, provider: str, model: str) -> Session:
        return Session.create(self.directory, cwd, provider, model, emit=self._broadcast)

    def open(self, session_id: str) -> Session:
        return Session.open(self.directory, session_id, emit=self._broadcast)

    def list(self, limit: int | None = None) -> list[SessionInfo]:
        if not self.directory.is_dir():
            return []
        infos: list[SessionInfo] = []
        for path in self.directory.glob("*.jsonl"):
            try:
                header = Session._read_header(path)
            except SessionError:
                continue
            stat = path.stat()
            infos.append(
                SessionInfo(
                    id=header.get("id", path.stem),
                    path=str(path),
                    created=header.get("created", ""),
                    updated=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
                    cwd=header.get("cwd", ""),
                    provider=header.get("provider", ""),
                    model=header.get("model", ""),
                    mtime=stat.st_mtime,
                )
            )
        infos.sort(key=lambda info: info.mtime, reverse=True)
        return infos[:limit] if limit else infos

    def latest(self) -> Session | None:
        infos = self.list(limit=1)
        return self.open(infos[0].id) if infos else None
