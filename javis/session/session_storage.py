"""Session persistence for javis.

Stores JSON snapshots under ``<workspace>/sessions/``:
    - ``latest.json``         — most recent session
    - ``session-{sid}.json``  — one file per session

Self-contained: no openharness utils依赖. ``atomic_write_text`` writes via a
temp file + rename so a crash mid-write cannot corrupt the latest snapshot.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from javis.core.messages import ConversationMessage, sanitize_conversation_messages
from javis.core.usage import UsageSnapshot
from javis.session.workspace import get_sessions_dir


def _session_dir(workspace: str | Path | None = None) -> Path:
    path = get_sessions_dir(workspace)
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_text(path: Path, data: str) -> None:
    """Write ``data`` to ``path`` atomically (temp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _persistable_tool_metadata(tool_metadata: dict[str, object] | None) -> dict[str, Any]:
    """Filter tool metadata down to JSON-persistable values."""
    if not tool_metadata:
        return {}
    persistable: dict[str, Any] = {}
    for key, value in tool_metadata.items():
        try:
            json.dumps(value)
            persistable[key] = value
        except (TypeError, ValueError):
            persistable[key] = str(value)
    return persistable


def _sanitize_snapshot_payload(payload: Any) -> dict[str, Any]:
    """Validate and normalize a loaded snapshot payload."""
    if not isinstance(payload, dict):
        raise ValueError("snapshot payload is not a dict")
    return payload


def save_session_snapshot(
    *,
    cwd: str | Path,
    workspace: str | Path | None = None,
    model: str,
    system_prompt: str,
    messages: list[ConversationMessage],
    usage: UsageSnapshot,
    session_id: str | None = None,
    tool_metadata: dict[str, object] | None = None,
) -> Path:
    """Persist the latest javis session snapshot and return its path."""
    sid = session_id or uuid4().hex[:12]
    now = time.time()
    messages = sanitize_conversation_messages(messages)
    summary = ""
    for msg in messages:
        if msg.role == "user" and msg.text.strip():
            summary = msg.text.strip()[:80]
            break

    payload = {
        "app": "javis",
        "session_id": sid,
        "cwd": str(Path(cwd).resolve()),
        "model": model,
        "system_prompt": system_prompt,
        "messages": [m.model_dump(mode="json") for m in messages],
        "usage": usage.model_dump(),
        "tool_metadata": _persistable_tool_metadata(tool_metadata),
        "created_at": now,
        "summary": summary,
        "message_count": len(messages),
    }
    data = json.dumps(payload, indent=2) + "\n"
    latest_path = _session_dir(workspace) / "latest.json"
    atomic_write_text(latest_path, data)
    session_path = _session_dir(workspace) / f"session-{sid}.json"
    atomic_write_text(session_path, data)
    return latest_path


def load_latest(workspace: str | Path | None = None) -> dict[str, Any] | None:
    path = _session_dir(workspace) / "latest.json"
    if not path.exists():
        return None
    return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))


def list_snapshots(workspace: str | Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    session_dir = _session_dir(workspace)
    sessions: list[dict[str, Any]] = []
    for path in sorted(session_dir.glob("session-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sessions.append(
            {
                "session_id": data.get("session_id", path.stem.replace("session-", "")),
                "summary": data.get("summary", ""),
                "message_count": data.get("message_count", len(data.get("messages", []))),
                "model": data.get("model", ""),
                "created_at": data.get("created_at", path.stat().st_mtime),
            }
        )
        if len(sessions) >= limit:
            break
    return sessions


def load_by_id(workspace: str | Path | None, session_id: str) -> dict[str, Any] | None:
    path = _session_dir(workspace) / f"session-{session_id}.json"
    if path.exists():
        return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))
    latest = load_latest(workspace)
    if latest and (latest.get("session_id") == session_id or session_id == "latest"):
        return latest
    return None


def export_session_markdown(
    *,
    cwd: str | Path,
    workspace: str | Path | None = None,
    messages: list[ConversationMessage],
) -> Path:
    del cwd
    path = _session_dir(workspace) / "transcript.md"
    parts = ["# javis Session Transcript"]
    for message in messages:
        parts.append(f"\n## {message.role.capitalize()}\n")
        text = message.text.strip()
        if text:
            parts.append(text)
    atomic_write_text(path, "\n".join(parts).strip() + "\n")
    return path


class JavisSessionBackend:
    """Session backend rooted in ``<workspace>/sessions``."""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self._workspace = workspace

    def get_session_dir(self, cwd: str | Path) -> Path:
        del cwd
        return _session_dir(self._workspace)

    def save_snapshot(
        self,
        *,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        messages: list[ConversationMessage],
        usage: UsageSnapshot,
        session_id: str | None = None,
        tool_metadata: dict[str, object] | None = None,
    ) -> Path:
        return save_session_snapshot(
            cwd=cwd,
            workspace=self._workspace,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            usage=usage,
            session_id=session_id,
            tool_metadata=tool_metadata,
        )

    def load_latest(self, cwd: str | Path) -> dict[str, Any] | None:
        del cwd
        return load_latest(self._workspace)

    def list_snapshots(self, cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
        del cwd
        return list_snapshots(self._workspace, limit=limit)

    def load_by_id(self, cwd: str | Path, session_id: str) -> dict[str, Any] | None:
        del cwd
        return load_by_id(self._workspace, session_id)

    def export_markdown(
        self,
        *,
        cwd: str | Path,
        messages: list[ConversationMessage],
    ) -> Path:
        return export_session_markdown(cwd=cwd, workspace=self._workspace, messages=messages)


__all__ = [
    "JavisSessionBackend",
    "atomic_write_text",
    "export_session_markdown",
    "list_snapshots",
    "load_by_id",
    "load_latest",
    "save_session_snapshot",
]
